from flask import Flask, render_template, request, redirect, url_for, flash
import webbrowser
import threading
from pymodbus.client import ModbusTcpClient, ModbusSerialClient

def open_browser():
    webbrowser.open_new('http://127.0.0.1:5000/')

def read_weight(ip, port, register):
    try:
        client = ModbusTcpClient(ip, port=port)
        client.connect()
        rr = client.read_holding_registers(register, 2, unit=1)
        client.close()
        if rr.isError():
            return None
        # Combine two 16-bit registers into a 32-bit float (adjust as needed)
        value = (rr.registers[0] << 16) + rr.registers[1]
        return value / 100.0  # Adjust scaling as per your sensor
    except Exception as e:
        return None


def get_all_weights(sensors):
    weights = []
    for sensor in sensors:
        weight = read_weight(sensor["ip"], sensor["port"], sensor["register"])
        weights.append({"name": sensor["name"], "weight": weight})
    return weights


app = Flask(__name__)
app.secret_key = "a_secret_key"

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/weights', methods=['GET', 'POST'])
def weights():
    global_weight = get_global_weight(sensors, tare_values)
    return render_template('weights.html', global_weight=global_weight)

def control_motor(ip, port, register, value):
    try:
        client = ModbusTcpClient(ip, port=port)
        client.connect()
        # Write a single register (adjust as needed for your motor)
        rr = client.write_register(register, value, unit=1)
        client.close()
        return not rr.isError()
    except Exception as e:
        return False

def control_motor_rs485(port, slave_id, register, value):
    try:
        client = ModbusSerialClient(
            method='tcp',
            port=port,
            baudrate=9600,      # Adjust as needed
            parity='N',         # 'N'one, 'E'ven, 'O'dd
            stopbits=1,
            bytesize=8,
            timeout=1
        )
        client.connect()
        rr = client.write_register(register, value, unit=slave_id)
        client.close()
        return not rr.isError()
    except Exception as e:
        return False

# --------------------------------------------------------sensor configuration---------------------------------------------------------
sensors = [
        {"name": "Sensor 1", "ip": "192.168.1.10", "port": 502, "register": 0},
        {"name": "Sensor 2", "ip": "192.168.1.11", "port": 502, "register": 0},
        {"name": "Sensor 3", "ip": "192.168.1.12", "port": 502, "register": 0},
    ]

tare_values = [0, 0, 0]

#---------------------------------------------------------motor configuration---------------------------------------------------------
MOTOR_IP = "192.168.1.20"
MOTOR_PORT = 502
MOTOR_REGISTER = 1  # Adjust as needed

MOTOR_PROFILES = {
    "profile1": 100, 
    "profile2": 200,
    "profile3": 300, 
}

MOTOR_SERIAL_PORT = "/dev/ttyUSB0"  # Adjust to your RS485 adapter
MOTOR_SLAVE_ID = 1                  # Set to your motor's Modbus address
MOTOR_REGISTER = 1                  # Adjust as needed



@app.route('/motor/<profile>', methods=['POST'])
def motor_control(profile):
    value = MOTOR_PROFILES.get(profile)
    if value is not None:
        success = control_motor_rs485(MOTOR_SERIAL_PORT, MOTOR_SLAVE_ID, MOTOR_REGISTER, value)
        if success:
            flash(f"Motor set to {profile}", "success")
        else:
            flash("Failed to control motor", "error")
    else:
        flash("Invalid profile", "error")
    return redirect(url_for('home'))

@app.route('/profiles')
def profiles():
    return render_template('profiles.html')



@app.route('/tare_all', methods=['POST'])
def tare_all():
    success = True
    for idx, sensor in enumerate(sensors):
        weight = read_weight(sensor["ip"], sensor["port"], sensor["register"])
        if weight is not None:
            tare_values[idx] = weight
        else:
            success = False
            flash(f"Failed to read {sensor['name']} for tare", "error")
    if success:
        flash("Tare set for all sensors", "success")
    return redirect(url_for('weights'))

def get_global_weight(sensors, tare_values):
    total = 0
    valid = True
    for idx, sensor in enumerate(sensors):
        weight = read_weight(sensor["ip"], sensor["port"], sensor["register"])
        if weight is not None:
            total += (weight - tare_values[idx])
        else:
            valid = False
    return total if valid else None

if __name__ == '__main__':
    # threading.Timer(1.5, open_browser).start()
    app.run(debug=True)
