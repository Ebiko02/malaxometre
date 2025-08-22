from pymodbus.client import ModbusSerialClient, ModbusTcpClient
import time
import math
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import threading
import csv
from datetime import datetime
import os
import threading

MOTOR_SERIAL_PORT = "COM4"  
MOTOR_SLAVE_ID = 1
MOTOR_FREQUENCY_REGISTER = 8193   # Register for motor frequency
MOTOR_ON_OFF_REGISTER = 8192 # Register to turn motor on/off
COMM_FREQ_REG = 0x2001      # Communication frequency setting
TORQUE_SETTING_REG = 0x2004  # Torque setting value
UPPER_LIMIT_TORQUE_REG = 0x2007  # Upper limit electromotion torque
V_F_VOLTAGE_REG = 0x200C     # V/F separation voltage setting
CONTROL_COMMAND_REG = 0x2009 # Special control command
VOLTAGE_REG = 0x3003
CURRENT_REG = 0x3004
PF = 0.85

SENSOR_TCP_IP = "192.168.0.100"
SENSOR_TCP_PORT = 502
SENSOR_SLAVE_ID = 1
GROSS_WEIGHT_REGISTER = 0x007E  # Register for the weight the sensor reads
NET_WEIGHT_REGISTER   = 0x0082  # Register for the net weight (after tare)

CMD_REG = 0x0090
RESP_REG = 0x0091
MEAS_STATUS = 0x007D

CMD_TARE = 0x00D4
CMD_CANCEL_TARE = 0x00D5

# ---------------- Tare state -----------------
_tare_lock = threading.Lock()
_tare_offset = 0.0  # stored gross weight at last tare (kg)

def get_tare_offset():
    with _tare_lock:
        return _tare_offset

def set_tare_offset(value: float):
    global _tare_offset
    with _tare_lock:
        _tare_offset = value
        print(f"[tare] Offset set to {value:.4f} kg")


def control_motor_rs485(client, port: str, slave_id: int, register: int, value: int) -> bool:
    """
    Control the motor speed via RS485 Modbus.
    """
    if client.connect():
        try:
            # Use write_registers (plural) with slave parameter to match working code
            rr = client.write_registers(address=register, values=[value])
            if hasattr(rr, "isError") and rr.isError():
                print(f"Modbus error writing to 0x{register:X}: {rr}")
                return False
            return True
        except Exception as e:
            print(f"Error writing to register 0x{register:X}: {e}")
            return False
    else:
        print("Failed to connect to the Modbus client.")
        return False


def run_profile(client, profile_steps: list, profile_name: str = "profile"):
    """
    Run a motor profile (list of (Hz, seconds)).
    Logs cumulative energy to CSV: timestamp, energy_Wh.
    Returns total energy (Wh).
    """
    # Prepare log file
    ts_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in profile_name)
    # Always log inside the same folder as this script to avoid ambiguity
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "energy_logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception as e:
        print("Could not create log dir:", e)

    # Ensure unique filename even if two profiles start in same second
    base_filename = f"{ts_prefix}_{safe_name}.csv"
    log_path = os.path.join(log_dir, base_filename)
    counter = 1
    while os.path.exists(log_path):
        log_path = os.path.join(log_dir, f"{ts_prefix}_{safe_name}_{counter}.csv")
        counter += 1

    print(f"[run_profile] Working directory: {os.getcwd()}")
    print(f"[run_profile] Logging energy to: {log_path}")

    total_energy_Wh = 0.0
    profile_start = time.time()

    # Open once for whole profile
    with open(log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_iso", "energy_Wh"])

        # Helper to log current cumulative energy
        def log_energy(cum_energy):
            writer.writerow([datetime.now().isoformat(timespec="seconds"), f"{cum_energy:.6f}"])
            f.flush()

        for idx, (hz, s) in enumerate(profile_steps, start=1):
            print(f"Running step {idx}: {hz} Hz for {s} s")
            if hz != 0 and s > 0:
                # Track energy during this step with incremental logging
                step_energy = intelligent_load_compensation(
                    client,
                    target_freq=hz,
                    slave_id=MOTOR_SLAVE_ID,
                    duration=s,
                    energy_log_callback=log_energy,
                    cumulative_base=total_energy_Wh
                )
                total_energy_Wh += step_energy
                print(f"Step {hz}Hz ({s}s) energy: {step_energy:.4f} Wh (cumulative {total_energy_Wh:.4f} Wh)")
                log_energy(total_energy_Wh)  # ensure final value for step
            else:
                # Rest / stop step; ensure motor is stopped and log unchanged energy start & end
                motor_stop(client)
                log_energy(total_energy_Wh)
                if s > 0:
                    time.sleep(s)
                log_energy(total_energy_Wh)

        # Final motor stop & final log entry
        motor_stop(client)
        log_energy(total_energy_Wh)

    print(f"Profile complete. Total energy: {total_energy_Wh:.4f} Wh. Logged to {log_path}")
    try:
        client.close()
    except Exception:
        pass
    return total_energy_Wh


def read_voltage_current(client):
    """
    Read voltage and current from the motor using Modbus.
    """
    try:
        client.connect()
        v_resp = client.read_holding_registers(address=VOLTAGE_REG, count=1)
        i_resp = client.read_holding_registers(address=CURRENT_REG, count=1)

        # Check for Modbus errors
        if (hasattr(v_resp, "isError") and v_resp.isError()) or (hasattr(i_resp, "isError") and i_resp.isError()):
            print("Error reading voltage/current:", v_resp, i_resp)
            return None, None

        voltage = v_resp.registers[0]
        current = i_resp.registers[0] / 10
        # Debug output for tracing
        print(f"[read_voltage_current] Voltage: {voltage} V, Current: {current} A")
        return voltage, current
    
    finally:
        try:
            client.close()
        except Exception:
            pass

def calculate_power(volatge, current, pf):
    """
    Calculate the power in watts using voltage, current, and power factor.
    Args:
        volatge: Voltage in volts.
        current: Current in amperes.
        pf: Power factor (between 0 and 1).
    Returns:
        Power in watts, or None if voltage or current is None.
    """ 

    if volatge is not None and current is not None:
        power = volatge * current * pf
        return power
    else:
        return None


def no_load_energy(client, frequency: int, period: int = 30) -> float:
    """
    Calculate no-load energy in Wh over `period` seconds at `frequency` Hz.
    """
    # Start the motor at the desired frequency
    client.connect()
    control_motor_rs485(client, MOTOR_SERIAL_PORT, MOTOR_SLAVE_ID, MOTOR_FREQUENCY_REGISTER, frequency * 100)
    control_motor_rs485(client, MOTOR_SERIAL_PORT, MOTOR_SLAVE_ID, MOTOR_ON_OFF_REGISTER, 1)
    time.sleep(5)  # Allow motor to stabilize

    start_time = time.time()
    last_time = start_time
    total_energy_Wh = 0.0

    while time.time() - start_time < period:
        # Read voltage and current
        voltage_response = client.read_holding_registers(address=VOLTAGE_REG, count=1)
        current_response = client.read_holding_registers(address=CURRENT_REG, count=1)
        # power_response = client.read_holding_registers(address=0x3006, count=1, unit=MOTOR_SLAVE_ID)
        # torque_response = client.read_holding_registers(address=0x3007, count=1, slave=MOTOR_SLAVE_ID)

        if not voltage_response.isError() and not current_response.isError():
            voltage = voltage_response.registers[0]
            current = current_response.registers[0] / 10
            power = voltage * current * PF  * math.sqrt(3)# Calculated power in W

            
            print(f"Voltage: {voltage} V, Current: {current} A, Power: {power:.2f} W")

            # Integrate energy (Wh)
            now = time.time()
            dt = now - last_time  # seconds
            total_energy_Wh += power * dt / 3600.0
            last_time = now
        else:
            print("Error reading voltage or current:", voltage_response, current_response)

        time.sleep(0.1)

    # Stop the motor
    motor_stop(client)
    return total_energy_Wh


def intelligent_load_compensation(client, target_freq, slave_id, duration,
                                  energy_log_callback=None, cumulative_base=0.0):
    """
    Combined torque and V/F control.
    energy_log_callback: optional callable(cumulative_energy_wh) each loop.
    cumulative_base: energy already accumulated before this step.
    Returns step energy (Wh).
    """
    freq_units = int(target_freq * 100)
    client.write_registers(address=COMM_FREQ_REG, values=[freq_units])
    client.write_registers(address=UPPER_LIMIT_TORQUE_REG, values=[1500])  # 150%
    client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[1])

    start_time = time.time()
    last_time = start_time
    baseline_current = None
    step_energy_Wh = 0.0

    while time.time() - start_time < duration:
        voltage, current = read_voltage_current(client)
        if voltage is None or current is None:
            print("Read failed, skipping cycle")
            time.sleep(0.8)
            continue

        # Energy integration
        now = time.time()
        dt = now - last_time
        power = voltage * current * PF * math.sqrt(3)
        step_energy_Wh += power * dt / 3600.0
        last_time = now

        # Per-loop debug print to help with tracing
        print(f"[intelligent_load_compensation] V={voltage} V, I={current} A, P={power:.2f} W, step_energy={step_energy_Wh:.6f} Wh")

        # Establish baseline current on first valid reading
        if baseline_current is None:
            if current <= 0:
                print("Waiting baseline current...")
                time.sleep(0.5)
                continue
            baseline_current = current
            print(f"Baseline current: {baseline_current:.2f} A")
            if energy_log_callback:
                energy_log_callback(cumulative_base + step_energy_Wh)
            time.sleep(0.5)
            continue

        # Adjust frequency/torque if load increases significantly
        current_ratio = current / baseline_current if baseline_current > 0 else 1
        if current_ratio > 2:
            boosted_freq_hz = min(target_freq + current_ratio, target_freq * 1.5)
            boosted_freq_units = int(boosted_freq_hz * 100)
            client.write_registers(address=COMM_FREQ_REG, values=[boosted_freq_units])
            torque_boost = min(int(1000 * current_ratio), 2000)
            client.write_registers(address=TORQUE_SETTING_REG, values=[torque_boost])
            print(f"Comp -> Freq {boosted_freq_hz:.2f}Hz Torque {torque_boost/10:.1f}% Ratio {current_ratio:.2f}")

        if energy_log_callback:
            energy_log_callback(cumulative_base + step_energy_Wh)

        time.sleep(0.3)

    client.write_registers(address=COMM_FREQ_REG, values=[0])
    return step_energy_Wh

def motor_stop(client):
    """
    Stop the motor by setting frequency to 0.
    """
    control_motor_rs485(client, MOTOR_SERIAL_PORT, MOTOR_SLAVE_ID, MOTOR_FREQUENCY_REGISTER, 0)
    control_motor_rs485(client, MOTOR_SERIAL_PORT, MOTOR_SLAVE_ID, MOTOR_ON_OFF_REGISTER, 0)

def to_signed_32(val):
    return val if val < 2**31 else val - 2**32

def read_weight(ip : str, port : int, gross : bool = True) -> float: 
    """"
    Read weight from the sensor via Modbus TCP.
    Args:
        ip: IP address of the sensor.
        port: Port number of the sensor.
        gross: If True, read gross weight; if False, read net weight.
    Returns:
        Weight in g, or None if read fails.
    """

    target_register = GROSS_WEIGHT_REGISTER if gross else NET_WEIGHT_REGISTER
    client = ModbusTcpClient(ip, port=port)
    
    if not client.connect():
        print("Failed to connect to weight sensor.")
        raise IOError(f"Failed to connect to weight sensor at, IP: {ip}, Port: {port}")
    
    try:
        t = client.read_input_registers(target_register, count=2)
        regs = t.registers
        # LSB first (little-endian)
        weight = regs[0] + (regs[1] << 16)
        weight = to_signed_32(weight)
        # the weight is in 0.1 g units
        weight = float(weight) * 0.1
        weight = round(weight, 1)  # round to 0.1 g precision
        return weight
    
    finally:
        client.close()



def tare_weight(ip: str, port: int = 502) -> None:
    """
    Send tare command to the weight sensor via Modbus TCP.
        Args:
            ip: IP address of the sensor.
            port: Port number of the sensor.
        Returns:
            None
        Raises:
            IOError if tare command fails.
    """
    client = ModbusTcpClient("192.168.0.100", port=502)
    if client.connect():
        # 1) Clear command register
        client.write_register(address=CMD_REG, value=0)

        # 2) Issue TARE command
        client.write_register(address=CMD_REG, value=CMD_TARE)

        # 3) (optional) read response register until nonzero then back to 0
        #    0 typically means "no error / idle" once completed.
        for _ in range(20):
            r = client.read_holding_registers(address=RESP_REG, count=1)
            code = r.registers[0] if hasattr(r, "registers") else 0xFFFF
            if code == 0:  # idle/success
                break
            time.sleep(0.1)

        # 4) Clear command register for next use
        client.write_register(address=CMD_REG, value=0)

        client.close()

# ---------------- Water percentage tracking -----------------
import threading

_water_state_lock = threading.Lock()
_powder_mass = None  # mass in grams when "Adding Water" was clicked
_water_mode_active = False

def get_powder_mass():
    with _water_state_lock:
        return _powder_mass

def set_powder_mass(mass_g: float):
    global _powder_mass
    with _water_state_lock:
        _powder_mass = mass_g
        print(f"[water] Powder mass set to {mass_g:.1f} g")

def is_water_mode_active():
    with _water_state_lock:
        return _water_mode_active

def set_water_mode(active: bool):
    global _water_mode_active
    with _water_state_lock:
        _water_mode_active = active
        if not active:
            _powder_mass = None
        print(f"[water] Water mode {'activated' if active else 'deactivated'}")

def compute_water_percentage(powder_mass_g: float, total_mass_g: float) -> float:
    """
    Compute water percentage relative to the powder mass.
    Returns percentage (e.g. 8.5 means 8.5%) rounded to 0.1, or 0.0 if invalid.
    """
    if not powder_mass_g or powder_mass_g <= 0:
        return 0.0
    
    water_mass = total_mass_g - powder_mass_g
    if water_mass <= 0:
        return 0.0
    
    percentage = (water_mass / powder_mass_g) * 100.0
    return round(percentage, 1)

def get_current_water_data(ip: str = SENSOR_TCP_IP, port: int = SENSOR_TCP_PORT) -> dict:
    """
    Read current weight and compute water percentage if in water mode.
    Returns: {
        'current_mass': float,
        'powder_mass': float|None,
        'water_mass': float|None,
        'water_percentage': float|None,
        'water_mode_active': bool
    }
    """
    try:
        current_mass = read_weight(ip, port, gross=False)  # use net weight (after tare)
        powder_mass = get_powder_mass()
        water_mode = is_water_mode_active()
        
        if water_mode and powder_mass:
            water_mass = current_mass - powder_mass
            water_pct = compute_water_percentage(powder_mass, current_mass)
            return {
                'current_mass': current_mass,
                'powder_mass': powder_mass,
                'water_mass': max(0, water_mass),  # don't show negative water
                'water_percentage': water_pct,
                'water_mode_active': True
            }
        else:
            return {
                'current_mass': current_mass,
                'powder_mass': None,
                'water_mass': None,
                'water_percentage': None,
                'water_mode_active': False
            }
    except Exception as e:
        print(f"Error reading weight data: {e}")
        return {
            'current_mass': None,
            'powder_mass': None,
            'water_mass': None,
            'water_percentage': None,
            'water_mode_active': is_water_mode_active()
        }

# ---------------- Weight history -----------------
import threading
from datetime import datetime
from collections import deque

_history_lock = threading.Lock()
_weight_history = deque(maxlen=50)  # Keep last 50 measurements

def add_weight_to_history(weight: float, timestamp: str = None):
    """Add a weight measurement to history"""
    if timestamp is None:
        timestamp = datetime.now().strftime('%H:%M:%S')
    
    with _history_lock:
        _weight_history.appendleft({
            'weight': weight,
            'at': timestamp
        })

def get_recent_weight_history(limit: int = 10) -> list:
    """Get recent weight measurements (most recent first)"""
    with _history_lock:
        return list(_weight_history)[:limit]

def clear_weight_history():
    """Clear all weight history"""
    with _history_lock:
        _weight_history.clear()


