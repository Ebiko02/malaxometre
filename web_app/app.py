from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import webbrowser
import threading
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
import time
from datetime import datetime  # Add this import
import functions

app = Flask(__name__)
app.secret_key = "a_secret_key"

SENSOR_TCP_IP = "192.168.0.100"
SENSOR_TCP_PORT = 502
WEIGHT_REGISTER = 130
SENSOR_SLAVE_ID = 1 

motor_client = ModbusSerialClient(
    port='COM4',
    baudrate=19200,
    parity='E',
    stopbits=1,
    bytesize=8,
    timeout=1
)

sensor_client = ModbusTcpClient(SENSOR_TCP_IP, port=SENSOR_TCP_PORT)

@app.route('/')
def home():
    return render_template('home.html')


# -------------------------------------------------------------- motor profiles --------------------------------------------------------------
MOTOR_PROFILES = {
    "DUO-mix": [(10, 10), (15, 4), (0, 5)],  # (Hz, seconds)
    "profile_2": [(12, 5), (0, 5), (4, 7)],
    "profile_3": [(8, 1), (20, 2), (0, 0)]
}

@app.route('/motor_control', methods=['POST'])
def motor_control():
    profile = request.args.get('profile')
    print("DEBUG: motor_control called, profile param =", profile)
    if profile in MOTOR_PROFILES:
        profile_steps = MOTOR_PROFILES[profile]
    else:
        flash('Invalid profile selected', 'error')
        return redirect(url_for('profiles'))

    try:
        energy_Wh = functions.run_profile(motor_client, profile_steps, profile_name=profile)
        flash(f'Profile {profile} executed successfully. Energy used: {energy_Wh:.2f} Wh', 'success')
    except Exception as e:
        flash(f'Error executing profile {profile}: {e}', 'error')

    return redirect(url_for('profiles'))  # <-- redirect to profiles page

@app.route('/profile_duration/<profile>', methods=['GET'])
def profile_duration(profile):
    duration = sum(step[1] for step in MOTOR_PROFILES.get(profile, []))
    return jsonify({'duration': duration})

@app.route('/profiles', methods=['GET'])
def profiles():
    # Render the profiles page; pass MOTOR_PROFILES if the template lists them
    return render_template('profiles.html', MOTOR_PROFILES=MOTOR_PROFILES)

#  -------------------------------------------------------------- no load power --------------------------------------------------------------
@app.route('/no_load_power', methods=['POST'])
def no_load_power():
    energy_Wh = None  # Initialize to handle exceptions
    try:
        frequency = 5 # Example frequency in Hz
        period = 20  # Example period in seconds
        try:
            energy_Wh = functions.no_load_energy(motor_client, frequency, period)
        except Exception as e:
            flash(f'Error measuring no-load power: {e}', 'energy_error')
            return redirect(url_for('no_load_power'))
        flash(f'No-load energy measured: {energy_Wh:.2f} Wh', 'success')
    except Exception as e:
        flash(f'Error measuring no-load power: {e}', 'energy_error')

    return render_template('energy.html', energy_Wh=energy_Wh)


@app.route('/energy', methods=['GET'])
def energy():
    # Initial load of the energy page
    return render_template('energy.html', energy_Wh=None)

# -------------------------------------------------------------- custom profile --------------------------------------------------------------
@app.route('/custom_profile', methods=['POST'])
def custom_profile():
    try:
        num_phases = int(request.form['num_phases'])
        profile_steps = []
        for i in range(num_phases):
            freq = int(request.form[f'freq_{i}'])
            seconds = int(request.form[f'seconds_{i}'])
            profile_steps.append((freq, seconds))

        functions.run_profile(motor_client, profile_steps)
        flash('Custom profile executed successfully', 'success')
    except Exception as e:
        flash(f'Error executing custom profile: {e}', 'error')

    return redirect(url_for('home'))

@app.route('/profile_status', methods=['GET'])
def profile_status():
    # Example implementation for status tracking
    # Replace with actual logic if needed
    return jsonify({'running': False})

@app.route('/profil_custom', methods=['GET', 'POST'])
def profil_custom():
    if request.method == 'POST':
        try:
            num_phases = int(request.form['num_phases'])
            profile_steps = []
            for i in range(num_phases):
                freq = int(request.form[f'freq_{i}'])
                seconds = int(request.form[f'seconds_{i}'])
                profile_steps.append((freq, seconds))
            
            energy_Wh = functions.run_profile(motor_client, profile_steps)
            flash(f'Profil personnalisé exécuté avec succès. Énergie utilisée: {energy_Wh:.2f} Wh', 'success')
        except Exception as e:
            flash(f'Erreur lors de l\'exécution du profil personnalisé : {e}', 'error')
        return redirect(url_for('profil_custom'))
    # GET: just render the page
    return render_template('profil_custom.html')

# -------------------------------------------------------------- weight sensor --------------------------------------------------------------
@app.route('/weights', methods=['GET', 'POST'])
def weights():
    if request.method == 'POST':
        try:
            # Get current water data (includes regular weight + water percentage if active)
            water_data = functions.get_current_water_data()
            current_weight = water_data['current_mass']
            timestamp = datetime.now().strftime('%H:%M:%S')
            
            if current_weight is not None:
                # Add to history
                functions.add_weight_to_history(current_weight, timestamp)
                
                # Check if this is an AJAX request
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'weight': current_weight,
                        'timestamp': timestamp,
                        'history': functions.get_recent_weight_history(),
                        'water_data': water_data  # Include water percentage data
                    })
                else:
                    # Traditional form submission
                    if water_data['water_mode_active'] and water_data['water_percentage']:
                        flash(f'Poids: {current_weight:.1f} g | Eau: {water_data["water_percentage"]:.1f}%', 'success')
                    else:
                        flash(f'Poids courant: {current_weight:.1f} g', 'success')
                    return redirect(url_for('weights'))
            else:
                raise Exception("Impossible de lire le poids")
                
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': str(e), 'weight': None, 'water_data': None}), 500
            else:
                flash(f'Erreur de lecture: {e}', 'error')
                return redirect(url_for('weights'))
    
    # GET request - render template with water data
    water_data = functions.get_current_water_data()
    return render_template('weights.html', 
                         global_weight=water_data.get('current_mass'),
                         water_data=water_data,
                         weight_history=functions.get_recent_weight_history())

@app.route('/water_mode', methods=['POST'])
def toggle_water_mode():
    """Toggle water percentage mode on/off"""
    try:
        action = request.form.get('action')  # 'start' or 'stop'
        
        if action == 'start':
            # Read current weight as powder mass
            current_weight = functions.read_weight(SENSOR_TCP_IP, SENSOR_TCP_PORT, gross=False)
            if current_weight <= 0:
                flash('Erreur: poids invalide. Assurez-vous que le bol avec la poudre est sur la balance.', 'error')
            else:
                functions.set_powder_mass(current_weight)
                functions.set_water_mode(True)
                flash(f'Mode eau activé. Poudre: {current_weight:.1f} g', 'success')
        
        elif action == 'stop':
            functions.set_water_mode(False)
            flash('Mode eau désactivé', 'success')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            water_data = functions.get_current_water_data()
            return jsonify({'success': True, 'water_data': water_data})
        
    except Exception as e:
        flash(f'Erreur: {e}', 'error')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500
    
    return redirect(url_for('weights'))

@app.route('/tare', methods=['POST'])
def tare():
    """Tare the weight sensor"""
    try:
        functions.tare_weight(SENSOR_TCP_IP, SENSOR_TCP_PORT)
        flash('Balance tarée avec succès', 'success')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Balance tarée'})
            
    except Exception as e:
        flash(f'Erreur lors du tarage: {e}', 'error')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500
    
    return redirect(url_for('weights'))



if __name__ == '__main__':
    # threading.Timer(1.5, open_browser).start()
    app.run(debug=True, use_reloader=False)
    t=1
