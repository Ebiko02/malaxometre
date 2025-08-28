from pymodbus.client import ModbusSerialClient, ModbusTcpClient
import time
import math
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import threading
import csv
from datetime import datetime
import os
import threading
import json

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
PF = 0.85 # Power factor for power calculations
ETA_MOTOR = 0.92 # Motor efficiency for power calculations
OPERATION_SPEED_REGISTER = 0x3005  # Actual motor RPM
OPERATION_FREQUENCY_REGISTER = 0x3000  # Actual frequency
MOTOR_POLES = 4  # Adjust based on your motor
PID_SETTING_REG = 0x3008      # PID setting: -100.0% to 100.0% (unit: 0.1%)
PID_FEEDBACK_REG = 0x3009     # PID feedback: -100.0% to 100.0% (unit: 0.1%)
CLOSE_LOOP_SETTING_REG = 0x3008   # Same as PID setting
CLOSE_LOOP_FEEDBACK_REG = 0x3009  # Same as PID feedback
OUTPUT_TORQUE_REGISTER = 0x3007  # Output torque: -250.0~250.0% (0.1% units)

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


def load_motor_profiles():
    """Load motor profiles from JSON file"""
    try:
        profile_path = os.path.join(os.path.dirname(__file__), 'motor_profiles.json')
        with open(profile_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('motor_profiles', {})
    except FileNotFoundError:
        flash('Fichier motor_profiles.json introuvable', 'error')
        return {}
    except json.JSONDecodeError as e:
        flash(f'Erreur de format dans motor_profiles.json: {e}', 'error')
        return {}

def save_motor_profiles(profiles):
    """Save motor profiles to JSON file"""
    try:
        profile_path = os.path.join(os.path.dirname(__file__), 'motor_profiles.json')
        data = {'motor_profiles': profiles}
        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        flash(f'Erreur lors de la sauvegarde: {e}', 'error')
        return False

def convert_profile_format(profile_data):
    """
    Convert JSON format to tuple format for backward compatibility
    JSON: [{"frequency": 10, "duration": 10}, ...]
    Tuple: [(10, 10), ...]
    """
    return [(step["frequency"], step["duration"]) for step in profile_data]

def convert_to_json_format(profile_tuples):
    """
    Convert tuple format back to JSON format
    Tuple: [(10, 10), ...]
    JSON: [{"frequency": 10, "duration": 10}, ...]
    """
    return [{"frequency": freq, "duration": dur} for freq, dur in profile_tuples]


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


def run_profile(client, profile_steps=None, profile_name=None):
    """
    Run a motor profile with calibrated torque compensation by default
    """
    
    # If profile_steps are provided, use them directly (custom profile)
    if profile_steps is not None:
        print(f"🎯 Running custom profile with calibrated torque compensation: {profile_name or 'Unnamed'}")
        return execute_profile_steps_calibrated(client, profile_steps, profile_name or "Custom")
    
    # If only profile_name provided, look it up in saved profiles
    elif profile_name is not None:
        profiles = load_motor_profiles()
        if profile_name not in profiles:
            raise ValueError(f"Profile '{profile_name}' not found")
        
        profile_data = convert_profile_format(profiles[profile_name])
        print(f"🎯 Running saved profile with calibrated torque compensation: {profile_name}")
        return execute_profile_steps_calibrated(client, profile_data, profile_name)
    
    else:
        raise ValueError("Either profile_steps or profile_name must be provided")

def execute_profile_steps_calibrated(client, steps, name="Unknown"):
    """Execute profile steps using calibrated torque compensation"""
    print(f"🚀 Starting calibrated profile: {name}")
    print(f"📋 Profile has {len(steps)} steps")
    
    total_energy = 0.0
    
    for i, (frequency, duration) in enumerate(steps):
        print(f"\n🔸 Step {i+1}/{len(steps)}: {frequency}Hz for {duration}s")
        
        try:
            step_energy = calibrated_torque_step(
                client, 
                frequency, 
                duration,
                energy_log_callback=None,  # Can add logging here if needed
                cumulative_base=total_energy
            )
            total_energy += step_energy
            
        except Exception as e:
            print(f"❌ Error in step {i+1}: {e}")
            # Try basic execution as fallback
            try:
                step_energy = execute_single_step_basic(client, frequency, duration, cumulative_base=total_energy)
                total_energy += step_energy
                print(f"✅ Step {i+1} completed with basic control (fallback)")
            except Exception as e2:
                print(f"❌ Step {i+1} failed completely: {e2}")
                continue
        
        # Brief pause between steps
        if i < len(steps) - 1:
            print(f"⏸️  Pausing 2s before next step...")
            time.sleep(2)
    
    print(f"\n🏁 Profile '{name}' completed!")
    print(f"📊 Total energy consumed: {total_energy:.4f} Wh")
    
    return total_energy

# def run_profile(client, profile_steps=None, profile_name=None):
#     """
#     Run a motor profile - either from saved profiles or custom steps
    
#     Args:
#         client: Motor client
#         profile_steps: List of (frequency, duration) tuples for custom profiles
#         profile_name: Name of saved profile to look up, or descriptive name for custom
#     """
    
#     # If profile_steps are provided, use them directly (custom profile)
#     if profile_steps is not None:
#         print(f"Running custom profile: {profile_name or 'Unnamed'}")
#         return execute_profile_steps(client, profile_steps, profile_name or "Custom")
    
#     # If only profile_name provided, look it up in saved profiles
#     elif profile_name is not None:
#         profiles = load_motor_profiles()  # You'll need to import this function
#         if profile_name not in profiles:
#             raise ValueError(f"Profile '{profile_name}' not found")
        
#         profile_data = convert_profile_format(profiles[profile_name])
#         print(f"Running saved profile: {profile_name}")
#         return execute_profile_steps(client, profile_data, profile_name)
    
#     else:
#         raise ValueError("Either profile_steps or profile_name must be provided")

def execute_profile_steps(client, steps, name="Unknown"):
    """Execute the actual profile steps"""
    total_energy = 0.0
    
    try:
        for i, (frequency, duration) in enumerate(steps):
            print(f"Step {i+1}/{len(steps)}: {frequency}Hz for {duration}s")
            
            # Your existing step execution logic here
            step_energy = intelligent_load_compensation(
                client, 
                target_freq=frequency, 
                slave_id=1, 
                duration=duration,
                cumulative_base=total_energy
            )
            
            total_energy += step_energy
            print(f"Step {i+1} completed. Energy: {step_energy:.4f}Wh")
        
        print(f"Profile '{name}' completed. Total energy: {total_energy:.4f}Wh")
        return total_energy
        
    except Exception as e:
        print(f"Error executing profile '{name}': {e}")
        # Make sure motor is stopped
        try:
            client.write_registers(address=0x2001, values=[0])  # Stop motor
        except:
            pass
        raise

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
        power = volatge * current * pf * math.sqrt(3) * ETA_MOTOR
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
            power = calculate_power(voltage, current, PF)  # Calculated power in W
            if power is None:
                print("Power calculation failed, skipping cycle")
                time.sleep(0.1)
                continue

            
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
        power = calculate_power(voltage, current, PF)
        if power is None:
            print("Power calculation failed, skipping cycle")
            time.sleep(0.8)
            continue
        step_energy_Wh += power * dt / 3600.0
        last_time = now

        # Per-loop debug print to help with tracing
        print(f"[intelligent_load_compensation] V={voltage} V, I={current} A, P={power:.2f} W, step_energy={step_energy_Wh:.6f} Wh")

        # Establish baseline current on first valid reading
        if baseline_current is None:
            if current <= 0:
                print("Waiting baseline current...")
                time.sleep(0.1)
                continue
            baseline_current = current
            print(f"Baseline current: {baseline_current:.2f} A")
            if energy_log_callback:
                energy_log_callback(cumulative_base + step_energy_Wh)
            time.sleep(0.1)
            continue

        # Adjust frequency/torque if load increases significantly
        current_ratio = current / baseline_current if baseline_current > 0 else 1
        if current_ratio > 1:
            boosted_freq_hz = min(target_freq + current_ratio, target_freq * 1.5)
            boosted_freq_units = int(boosted_freq_hz * 100)
            client.write_registers(address=COMM_FREQ_REG, values=[boosted_freq_units])
            torque_boost = min(int(1000 * current_ratio), 2000)
            client.write_registers(address=TORQUE_SETTING_REG, values=[torque_boost])
            print(f"Comp -> Freq {boosted_freq_hz:.2f}Hz Torque {torque_boost/10:.1f}% Ratio {current_ratio:.2f}")

        if energy_log_callback:
            energy_log_callback(cumulative_base + step_energy_Wh)

        time.sleep(0.1)

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

def add_weight_to_history(weight: float, timestamp = None):
    """Add a weight measurement to history
    Args:
        weight: Weight in grams.
        timestamp: Optional timestamp string (HH:MM:SS). If None, use current time.
    Returns:
        None
    
    """
    if timestamp is None:
        timestamp = datetime.now().strftime('%H:%M:%S')
    
    with _history_lock:
        _weight_history.appendleft({
            'weight': weight,
            'at': timestamp
        })

def get_recent_weight_history(limit: int = 10) -> list:
    """
    Get recent weight measurements (most recent first)
    Args:
        limit: Maximum number of entries to return.
     Returns:
        List of dicts with 'weight' and 'at' keys.
    """
    with _history_lock:
        return list(_weight_history)[:limit]

def clear_weight_history():
    """Clear all weight history"""
    with _history_lock:
        _weight_history.clear()

def measure_no_load_power(client, frequency=10, duration=30):
    """
    Measure no-load power consumption at specified frequency and duration
    
    Args:
        client: Motor Modbus client
        frequency: Motor frequency in Hz (default: 10)
        duration: Measurement duration in seconds (default: 30)
    
    Returns:
        float: Energy consumption in Wh
    """
    if not client.connect():
        raise ConnectionError("Could not connect to motor")
    
    try:
        print(f"Starting no-load measurement: {frequency}Hz for {duration}s")
        
        # Start motor at specified frequency
        result = client.write_registers(address=MOTOR_FREQUENCY_REGISTER, values=[frequency * 100])
        if result.isError():
            raise Exception(f"Failed to set frequency: {result}")
        
        # Turn motor on
        result = client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[1])
        if result.isError():
            raise Exception(f"Failed to start motor: {result}")
        
        print(f"Motor started at {frequency}Hz")
        
        # Wait for stabilization
        time.sleep(2)
        
        # Measure power for specified duration
        start_time = time.time()
        power_readings = []
        
        while time.time() - start_time < duration:
            try:
                # Read voltage and current
                voltage_resp = client.read_holding_registers(address=VOLTAGE_REG, count=1)
                current_resp = client.read_holding_registers(address=CURRENT_REG, count=1)
                
                if not voltage_resp.isError() and not current_resp.isError():
                    voltage = voltage_resp.registers[0] / 10.0  # Assuming 0.1V scale
                    current = current_resp.registers[0] / 100.0  # Assuming 0.01A scale
                    
                    # Calculate instantaneous power (3-phase)
                    power = voltage * current * math.sqrt(3) * PF
                    power_readings.append(power)
                    
                    print(f"V={voltage:.1f}V, I={current:.2f}A, P={power:.1f}W")
                
                time.sleep(0.5)  # Sample every 500ms
                
            except Exception as e:
                print(f"Reading error: {e}")
                continue
        
        # Stop motor
        client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[0])
        print("Motor stopped")
        
        # Calculate average power and energy
        if power_readings:
            avg_power = sum(power_readings) / len(power_readings)
            energy_Wh = avg_power * duration / 3600
            print(f"Average power: {avg_power:.1f}W, Energy: {energy_Wh:.3f}Wh")
            return energy_Wh
        else:
            raise Exception("No valid power readings obtained")
    
    finally:
        try:
            # Ensure motor is stopped
            client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[0])
            client.close()
        except:
            pass

def load_torque_calibration():
    """Load torque calibration data from torque_values.txt"""
    try:
        torque_file = os.path.join(os.path.dirname(__file__), 'torque_values.txt')
        with open(torque_file, 'r') as f:
            content = f.read()
            # Parse the dictionary-like content
            torque_data = eval(content)  # Or use ast.literal_eval for safety
        return torque_data
    except Exception as e:
        print(f"Error loading torque calibration: {e}")
        return None

def get_calibrated_thresholds(frequency_hz, calibration_data=None):
    """
    Get torque thresholds for a given frequency using calibration data.
    Interpolates between calibrated points if exact frequency not found.
    """
    if calibration_data is None:
        calibration_data = load_torque_calibration()
        if calibration_data is None:
            return None
    
    # If exact frequency exists, return it
    if frequency_hz in calibration_data:
        return calibration_data[frequency_hz]['thresholds']
    
    # Find closest frequencies for interpolation
    frequencies = sorted([f for f in calibration_data.keys() if calibration_data[f] is not None])
    
    if frequency_hz <= frequencies[0]:
        return calibration_data[frequencies[0]]['thresholds']
    elif frequency_hz >= frequencies[-1]:
        return calibration_data[frequencies[-1]]['thresholds']
    else:
        # Interpolate between two closest frequencies
        lower_freq = max([f for f in frequencies if f < frequency_hz])
        upper_freq = min([f for f in frequencies if f > frequency_hz])
        
        lower_data = calibration_data[lower_freq]['thresholds']
        upper_data = calibration_data[upper_freq]['thresholds']
        
        # Linear interpolation
        ratio = (frequency_hz - lower_freq) / (upper_freq - lower_freq)
        
        interpolated = {
            'baseline': lower_data['baseline'] + ratio * (upper_data['baseline'] - lower_data['baseline']),
            'light': lower_data['light'] + ratio * (upper_data['light'] - lower_data['light']),
            'moderate': lower_data['moderate'] + ratio * (upper_data['moderate'] - lower_data['moderate']),
            'heavy': lower_data['heavy'] + ratio * (upper_data['heavy'] - lower_data['heavy']),
            'category': lower_data['category'],
            'light_offset': lower_data['light_offset'],
            'moderate_offset': lower_data['moderate_offset'],
            'heavy_offset': lower_data['heavy_offset']
        }
        
        return interpolated

def calibrated_torque_step(client, frequency, duration, energy_log_callback=None, cumulative_base=0.0):
    """
    Single step execution with calibrated torque compensation
    """
    # Load calibration data
    calibration_data = load_torque_calibration()
    if calibration_data is None:
        print(f"⚠️  No calibration data - using standard control for {frequency}Hz")
        return execute_single_step_basic(client, frequency, duration, energy_log_callback, cumulative_base)
    
    # Get calibrated thresholds
    thresholds = get_calibrated_thresholds(frequency, calibration_data)
    if thresholds is None:
        print(f"⚠️  No thresholds for {frequency}Hz - using standard control")
        return execute_single_step_basic(client, frequency, duration, energy_log_callback, cumulative_base)
    
    print(f"🔧 Calibrated Step: {frequency}Hz for {duration}s ({thresholds['category']})")
    
    # Setup motor with enhanced settings for low frequencies
    freq_units = int(frequency * 100)
    
    # Increase torque limit for very low frequencies
    if frequency <= 5:
        torque_limit = 2500  # 250% for very low freq
        base_torque = 1200   # Higher base torque
    elif frequency <= 10:
        torque_limit = 2200  # 220% for low freq
        base_torque = 1100
    else:
        torque_limit = 2000  # 200% for normal freq
        base_torque = 1000
    
    client.write_registers(address=COMM_FREQ_REG, values=[freq_units])
    client.write_registers(address=UPPER_LIMIT_TORQUE_REG, values=[torque_limit])
    client.write_registers(address=TORQUE_SETTING_REG, values=[base_torque])
    client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[1])
    
    # Control parameters
    start_time = time.time()
    last_time = start_time
    step_energy_Wh = 0.0
    
    # State tracking
    compensation_active = False
    load_applied = False
    aggressive_mode = frequency <= 8  # More aggressive for very low frequencies
    
    # Enhanced monitoring for low frequencies
    consecutive_high_torque = 0
    torque_history = []
    
    while time.time() - start_time < duration:
        voltage, current = read_voltage_current(client)
        if voltage is None or current is None:
            time.sleep(0.1)
            continue
        
        # Read torque
        try:
            torque_resp = client.read_holding_registers(address=OUTPUT_TORQUE_REGISTER, count=1)
            freq_resp = client.read_holding_registers(address=OPERATION_FREQUENCY_REGISTER, count=1)
            
            if torque_resp.isError():
                time.sleep(0.1)
                continue
                
            raw_torque = torque_resp.registers[0]
            if raw_torque > 32767:
                raw_torque -= 65536
            output_torque = raw_torque / 10.0
            
            actual_freq = freq_resp.registers[0] / 100.0 if not freq_resp.isError() else frequency
            
            # Track torque history for trend analysis
            torque_history.append(abs(output_torque))
            if len(torque_history) > 10:
                torque_history.pop(0)
            
        except Exception as e:
            print(f"Read error: {e}")
            time.sleep(0.1)
            continue
        
        # Energy calculation
        now = time.time()
        dt = now - last_time
        power = calculate_power(voltage, current, PF)
        if power is not None:
            step_energy_Wh += power * dt / 3600.0
        last_time = now
        elapsed = now - start_time
        
        # Load detection using calibrated thresholds
        current_torque_abs = abs(output_torque)
        
        # Enhanced load classification for low frequencies
        if aggressive_mode:
            # Lower thresholds and more sensitive detection for low freq
            light_threshold = max(thresholds['light'] * 0.7, thresholds['baseline'] + 2)
            moderate_threshold = max(thresholds['moderate'] * 0.8, thresholds['baseline'] + 5)
            heavy_threshold = max(thresholds['heavy'] * 0.9, thresholds['baseline'] + 8)
        else:
            light_threshold = thresholds['light']
            moderate_threshold = thresholds['moderate']
            heavy_threshold = thresholds['heavy']
        
        # Classify load level
        if current_torque_abs >= heavy_threshold:
            load_level = "HEAVY"
            load_detected = True
            consecutive_high_torque += 1
        elif current_torque_abs >= moderate_threshold:
            load_level = "MODERATE"
            load_detected = True
            consecutive_high_torque += 1
        elif current_torque_abs >= light_threshold:
            load_level = "LIGHT"
            load_detected = True
            consecutive_high_torque = max(0, consecutive_high_torque - 1)
        else:
            load_level = "NORMAL"
            load_detected = False
            consecutive_high_torque = 0
        
        # Detect load application or motor struggling
        torque_trend_increasing = False
        if len(torque_history) >= 5:
            recent_avg = sum(torque_history[-3:]) / 3
            older_avg = sum(torque_history[:3]) / 3
            torque_trend_increasing = recent_avg > older_avg * 1.2
        
        # Emergency stall detection for very low frequencies
        stall_risk = False
        if aggressive_mode and consecutive_high_torque >= 3:
            stall_risk = True
            print(f"⚠️  STALL RISK DETECTED - Consecutive high torque: {consecutive_high_torque}")
        
        if (load_detected and not load_applied) or stall_risk or torque_trend_increasing:
            if not load_applied:
                print(f"🚨 {load_level} LOAD DETECTED - Torque: {current_torque_abs:.1f}%")
            load_applied = True
            compensation_active = True
        
        # Active compensation with enhanced factors for low frequencies
        if compensation_active:
            # Much more aggressive compensation factors for low frequencies
            if frequency <= 3:  # Ultra-low speed - maximum compensation
                freq_boost_factor = 1.5 if load_level == "HEAVY" else 1.2 if load_level == "MODERATE" else 0.8
                torque_boost_factor = 2.2 if load_level == "HEAVY" else 1.9 if load_level == "MODERATE" else 1.6
                if stall_risk:
                    freq_boost_factor *= 1.5
                    torque_boost_factor *= 1.3
            elif frequency <= 5:  # Very low speed
                freq_boost_factor = 1.2 if load_level == "HEAVY" else 1.0 if load_level == "MODERATE" else 0.7
                torque_boost_factor = 2.0 if load_level == "HEAVY" else 1.7 if load_level == "MODERATE" else 1.4
                if stall_risk:
                    freq_boost_factor *= 1.3
                    torque_boost_factor *= 1.2
            elif frequency <= 8:  # Low speed
                freq_boost_factor = 1.0 if load_level == "HEAVY" else 0.8 if load_level == "MODERATE" else 0.5
                torque_boost_factor = 1.8 if load_level == "HEAVY" else 1.6 if load_level == "MODERATE" else 1.3
                if stall_risk:
                    freq_boost_factor *= 1.2
                    torque_boost_factor *= 1.1
            elif thresholds['category'] in ['VERY_LOW_SPEED', 'LOW_SPEED']:
                freq_boost_factor = 0.8 if load_level == "HEAVY" else 0.5 if load_level == "MODERATE" else 0.3
                torque_boost_factor = 1.6 if load_level == "HEAVY" else 1.4 if load_level == "MODERATE" else 1.2
            elif thresholds['category'] == 'MEDIUM_SPEED':
                freq_boost_factor = 0.6 if load_level == "HEAVY" else 0.4 if load_level == "MODERATE" else 0.2
                torque_boost_factor = 1.5 if load_level == "HEAVY" else 1.3 if load_level == "MODERATE" else 1.15
            else:  # HIGH_SPEED, VERY_HIGH_SPEED
                freq_boost_factor = 0.4 if load_level == "HEAVY" else 0.3 if load_level == "MODERATE" else 0.15
                torque_boost_factor = 1.8 if load_level == "HEAVY" else 1.5 if load_level == "MODERATE" else 1.3
            
            # Calculate compensation
            excess_torque = current_torque_abs - thresholds['baseline']
            excess_ratio = excess_torque / thresholds['baseline'] if thresholds['baseline'] > 0 else 0
            
            # More aggressive frequency boost for low frequencies
            if aggressive_mode:
                freq_boost = min(excess_ratio * frequency * freq_boost_factor + frequency * 0.3, frequency * freq_boost_factor)
            else:
                freq_boost = min(excess_ratio * frequency * freq_boost_factor, frequency * freq_boost_factor)
            
            compensated_freq = frequency + freq_boost
            
            # Enhanced torque boost
            torque_boost = min(base_torque * torque_boost_factor, torque_limit)
            
            # Additional boost for trend detection
            if torque_trend_increasing and aggressive_mode:
                compensated_freq *= 1.1
                torque_boost = min(torque_boost * 1.1, torque_limit)
                print(f"📈 Trend boost applied!")
            
            # Apply compensation
            comp_freq_units = int(compensated_freq * 100)
            client.write_registers(address=COMM_FREQ_REG, values=[comp_freq_units])
            client.write_registers(address=TORQUE_SETTING_REG, values=[int(torque_boost)])
            
            status_emoji = "🆘" if stall_risk else "🔧"
            print(f"{status_emoji} Compensation: {load_level} | Freq: {actual_freq:.1f}→{compensated_freq:.1f}Hz | Torque: {torque_boost/10:.0f}% | Consec: {consecutive_high_torque}")
            
            # Enhanced load removal detection with hysteresis
            if aggressive_mode:
                # Stricter criteria for load removal at low frequencies
                hysteresis = max(thresholds['light_offset'] * 0.5, 3)
                load_removed_threshold = thresholds['baseline'] + hysteresis
            else:
                hysteresis = thresholds['light_offset'] * 0.3
                load_removed_threshold = thresholds['baseline'] + hysteresis
            
            # Only consider load removed if torque is consistently low
            if current_torque_abs < load_removed_threshold and consecutive_high_torque == 0:
                if len(torque_history) >= 3 and all(t < load_removed_threshold for t in torque_history[-3:]):
                    print(f"✅ Load removed - returning to baseline")
                    compensation_active = False
                    load_applied = False
                    consecutive_high_torque = 0
                    
                    # Return to baseline
                    baseline_freq_units = int(frequency * 100)
                    client.write_registers(address=COMM_FREQ_REG, values=[baseline_freq_units])
                    client.write_registers(address=TORQUE_SETTING_REG, values=[base_torque])
        
        if energy_log_callback:
            energy_log_callback(cumulative_base + step_energy_Wh)
        
        time.sleep(0.05 if aggressive_mode else 0.1)  # Faster monitoring for low frequencies
    
    # Step completed - stop motor
    client.write_registers(address=COMM_FREQ_REG, values=[0])
    client.write_registers(address=TORQUE_SETTING_REG, values=[1000])
    client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[0])
    
    print(f"✅ Calibrated step completed: {frequency}Hz, {duration}s, {step_energy_Wh:.4f}Wh")
    return step_energy_Wh

def execute_single_step_basic(client, frequency, duration, energy_log_callback=None, cumulative_base=0.0):
    """
    Basic step execution without torque compensation (fallback)
    """
    print(f"🔄 Basic Step: {frequency}Hz for {duration}s")
    
    freq_units = int(frequency * 100)
    client.write_registers(address=COMM_FREQ_REG, values=[freq_units])
    client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[1])
    
    start_time = time.time()
    last_time = start_time
    step_energy_Wh = 0.0
    
    while time.time() - start_time < duration:
        voltage, current = read_voltage_current(client)
        if voltage is None or current is None:
            time.sleep(0.1)
            continue
        
        now = time.time()
        dt = now - last_time
        power = calculate_power(voltage, current, PF)
        if power is not None:
            step_energy_Wh += power * dt / 3600.0
        last_time = now
        
        if energy_log_callback:
            energy_log_callback(cumulative_base + step_energy_Wh)
        
        time.sleep(0.1)
    
    client.write_registers(address=COMM_FREQ_REG, values=[0])
    client.write_registers(address=MOTOR_ON_OFF_REGISTER, values=[0])
    
    return step_energy_Wh
