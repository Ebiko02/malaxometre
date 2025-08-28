from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from datetime import datetime
import json
import os
import functions

app = Flask(__name__)
app.secret_key = "a_secret_key"

SENSOR_TCP_IP = "192.168.0.100"
SENSOR_TCP_PORT = 502
WEIGHT_REGISTER = 130
SENSOR_SLAVE_ID = 1 
PF = 0.85  # Power factor constant

motor_client = ModbusSerialClient(
    port='COM4',
    baudrate=19200,
    parity='E',
    stopbits=1,
    bytesize=8,
    timeout=1
)

sensor_client = ModbusTcpClient(SENSOR_TCP_IP, port=SENSOR_TCP_PORT)

# -------------------------------------------------------------- motor profiles --------------------------------------------------------------

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

@app.route('/profiles', methods=['GET'])
def profiles():
    profiles = load_motor_profiles()
    # Convert to the format your template expects
    MOTOR_PROFILES = {name: convert_profile_format(data) for name, data in profiles.items()}
    return render_template('profiles.html', profiles=profiles, MOTOR_PROFILES=MOTOR_PROFILES)

@app.route('/')
def home():
    # If your home template uses MOTOR_PROFILES, add it here too
    profiles = load_motor_profiles()
    MOTOR_PROFILES = {name: convert_profile_format(data) for name, data in profiles.items()}
    return render_template('home.html', MOTOR_PROFILES=MOTOR_PROFILES, profiles=profiles)

@app.route('/motor_control', methods=['POST'])
def motor_control():
    # Get profile from form data instead of query parameters for POST requests
    profile = request.form.get('profile') or request.args.get('profile')
    print("DEBUG: motor_control called, profile param =", profile)
    
    if not profile:
        flash('No profile specified', 'error')
        return redirect(url_for('profiles'))
    
    profiles = load_motor_profiles()
    
    if profile in profiles:
        # Convert to tuple format for the functions
        profile_steps = convert_profile_format(profiles[profile])
        print(f"DEBUG: Found profile '{profile}' with {len(profile_steps)} steps")
    else:
        print(f"DEBUG: Available profiles: {list(profiles.keys())}")
        flash(f'Profile "{profile}" not found', 'error')
        return redirect(url_for('profiles'))

    try:
        # Check if torque calibration is available
        torque_data = functions.load_torque_calibration()
        if torque_data:
            print("🔧 Using calibrated torque compensation")
            flash_message = f'Profile {profile} executed with calibrated torque compensation.'
        else:
            print("⚠️  No torque calibration available - using standard control")
            flash_message = f'Profile {profile} executed with standard control (no calibration data).'
        
        energy_Wh = functions.run_profile(motor_client, profile_steps, profile_name=profile)
        flash(f'{flash_message} Energy used: {energy_Wh:.2f} Wh', 'success')
        
    except Exception as e:
        flash(f'Error executing profile {profile}: {e}', 'error')

    return redirect(url_for('profiles'))

@app.route('/profile_duration/<profile>', methods=['GET'])
def profile_duration(profile):
    profiles = load_motor_profiles()
    if profile in profiles:
        # Convert to tuple format and calculate duration
        profile_data = convert_profile_format(profiles[profile])
        total_duration = sum(duration for _, duration in profile_data)
        return jsonify({'duration': total_duration})
    return jsonify({'error': 'Profile not found'}), 404

@app.route('/profiles/edit', methods=['GET', 'POST'])
def edit_profiles():
    if request.method == 'POST':
        try:
            # Get the updated profiles from the form
            updated_profiles = {}
            profiles_data = request.get_json() if request.is_json else request.form
            
            # Process the updated profiles
            for profile_name, steps in profiles_data.items():
                if profile_name != 'action':  # Skip action field
                    updated_profiles[profile_name] = steps
            
            # Save to JSON file
            if save_motor_profiles(updated_profiles):
                flash('Profils sauvegardés avec succès', 'success')
                return jsonify({'success': True}) if request.is_json else redirect(url_for('profiles'))
            else:
                return jsonify({'error': 'Erreur lors de la sauvegarde'}), 500
            
        except Exception as e:
            flash(f'Erreur lors de la mise à jour: {e}', 'error')
            return jsonify({'error': str(e)}), 500 if request.is_json else redirect(url_for('profiles'))
    
    # GET request - show edit form
    profiles = load_motor_profiles()
    return render_template('edit_profiles.html', profiles=profiles)

@app.route('/profiles/add', methods=['POST'])
def add_profile():
    """Add a new motor profile"""
    try:
        data = request.get_json()
        profile_name = data.get('name')
        profile_steps = data.get('steps', [])
        
        if not profile_name:
            return jsonify({'error': 'Nom du profil requis'}), 400
        
        profiles = load_motor_profiles()
        profiles[profile_name] = profile_steps
        
        if save_motor_profiles(profiles):
            return jsonify({'success': True, 'message': f'Profil "{profile_name}" ajouté'})
        else:
            return jsonify({'error': 'Erreur lors de la sauvegarde'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/profiles/delete/<profile_name>', methods=['DELETE'])
def delete_profile(profile_name):
    """Delete a motor profile"""
    try:
        profiles = load_motor_profiles()
        
        if profile_name not in profiles:
            return jsonify({'error': 'Profil introuvable'}), 404
        
        del profiles[profile_name]
        
        if save_motor_profiles(profiles):
            return jsonify({'success': True, 'message': f'Profil "{profile_name}" supprimé'})
        else:
            return jsonify({'error': 'Erreur lors de la sauvegarde'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

#  -------------------------------------------------------------- energy measurement --------------------------------------------------------------

@app.route('/energy', methods=['GET', 'POST'])
def energy():
    """Energy measurement page with user-configurable parameters"""
    if request.method == 'POST':
        # This handles the form submission from energy.html
        return redirect(url_for('no_load_power'))
    
    # GET request - show the energy measurement form
    return render_template('energy.html')

@app.route('/no_load_power', methods=['GET', 'POST'])
def no_load_power():
    """Perform no-load power measurement with user-specified parameters"""
    if request.method == 'POST':
        try:
            # Get user input from the form
            frequency = int(request.form.get('frequency', 10))
            duration = int(request.form.get('duration', 30))
            
            # Validate inputs
            if frequency < 0 or frequency > 100:
                flash('Fréquence doit être entre 0 et 100 Hz', 'error')
                return render_template('energy.html', frequency=frequency, duration=duration)
            
            if duration < 1 or duration > 3600:
                flash('Durée doit être entre 1 et 3600 secondes', 'error')
                return render_template('energy.html', frequency=frequency, duration=duration)
            
            print(f"Starting no-load power measurement: {frequency}Hz for {duration}s")
            
            # Run the actual measurement with user parameters
            try:
                energy_Wh = functions.measure_no_load_power(
                    motor_client, 
                    frequency=frequency, 
                    duration=duration
                )
                
                # Calculate average power
                power_W = (energy_Wh * 3600) / duration if duration > 0 else 0
                
                flash(f'Mesure terminée. Puissance moyenne: {power_W:.1f}W, Énergie: {energy_Wh:.3f}Wh', 'success')
                
                return render_template('energy.html', 
                                     power_W=power_W, 
                                     energy_Wh=energy_Wh,
                                     frequency=frequency,
                                     duration=duration,
                                     measured_at=datetime.now().strftime('%H:%M:%S'),
                                     pf=PF)
            except AttributeError:
                # Fallback to old function if new one doesn't exist
                energy_Wh = functions.no_load_energy(motor_client, frequency, duration)
                power_W = (energy_Wh * 3600) / duration if duration > 0 else 0
                
                flash(f'Mesure terminée. Puissance moyenne: {power_W:.1f}W, Énergie: {energy_Wh:.3f}Wh', 'success')
                
                return render_template('energy.html', 
                                     power_W=power_W, 
                                     energy_Wh=energy_Wh,
                                     frequency=frequency,
                                     duration=duration,
                                     measured_at=datetime.now().strftime('%H:%M:%S'),
                                     pf=PF)
            
        except ValueError as ve:
            flash(f'Valeur invalide: {ve}', 'error')
            return render_template('energy.html')
        except Exception as e:
            flash(f'Erreur lors de la mesure: {e}', 'error')
            return render_template('energy.html')
    
    # GET request - show form with default values
    return render_template('energy.html', frequency=10, duration=30, pf=PF)

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
            
            # Pass a descriptive name for the custom profile
            energy_Wh = functions.run_profile(motor_client, profile_steps, profile_name="Custom Profile")
            flash(f'Profil personnalisé exécuté avec succès. Énergie utilisée: {energy_Wh:.2f} Wh', 'success')
        except Exception as e:
            flash(f'Erreur lors de l\'exécution du profil personnalisé : {e}', 'error')
        return redirect(url_for('profil_custom'))
    
    # GET: render with profiles data
    profiles = load_motor_profiles()
    MOTOR_PROFILES = {name: convert_profile_format(data) for name, data in profiles.items()}
    return render_template('profil_custom.html', MOTOR_PROFILES=MOTOR_PROFILES, profiles=profiles)

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

@app.route('/save_custom_profile', methods=['POST'])
def save_custom_profile():
    """Save a custom profile to the motor profiles JSON file"""
    try:
        data = request.get_json()
        profile_name = data.get('name', '').strip()
        profile_steps = data.get('steps', [])
        
        if not profile_name:
            return jsonify({'error': 'Nom du profil requis'}), 400
        
        if not profile_steps:
            return jsonify({'error': 'Au moins une étape requise'}), 400
        
        # Validate steps
        for i, step in enumerate(profile_steps):
            if not isinstance(step, dict) or 'frequency' not in step or 'duration' not in step:
                return jsonify({'error': f'Format d\'étape invalide à l\'index {i}'}), 400
            
            if step['duration'] <= 0:
                return jsonify({'error': f'Durée invalide à l\'étape {i+1}'}), 400
            
            if step['frequency'] < 0:
                return jsonify({'error': f'Fréquence invalide à l\'étape {i+1}'}), 400
        
        # Load existing profiles
        profiles = load_motor_profiles()
        
        # Check if profile name already exists
        if profile_name in profiles:
            return jsonify({'error': f'Un profil nommé "{profile_name}" existe déjà'}), 409
        
        # Add the new profile
        profiles[profile_name] = profile_steps
        
        # Save to JSON file
        if save_motor_profiles(profiles):
            return jsonify({
                'success': True, 
                'message': f'Profil "{profile_name}" sauvegardé avec succès',
                'profile_name': profile_name,
                'steps_count': len(profile_steps)
            })
        else:
            return jsonify({'error': 'Erreur lors de la sauvegarde'}), 500
            
    except Exception as e:
        return jsonify({'error': f'Erreur serveur: {str(e)}'}), 500

@app.route('/calibration_status')
def calibration_status():
    """Check torque calibration status"""
    try:
        torque_data = functions.load_torque_calibration()
        if torque_data:
            frequencies = sorted([f for f in torque_data.keys() if torque_data[f] is not None])
            return jsonify({
                'available': True,
                'frequencies': frequencies,
                'count': len(frequencies),
                'message': f'Calibration data available for {len(frequencies)} frequencies'
            })
        else:
            return jsonify({
                'available': False,
                'frequencies': [],
                'count': 0,
                'message': 'No calibration data available'
            })
    except Exception as e:
        return jsonify({
            'available': False,
            'error': str(e),
            'message': f'Error loading calibration: {e}'
        })

@app.context_processor
def inject_motor_profiles():
    """Make motor profiles available to all templates"""
    try:
        profiles = load_motor_profiles()
        MOTOR_PROFILES = {name: convert_profile_format(data) for name, data in profiles.items()}
        return {
            'MOTOR_PROFILES': MOTOR_PROFILES,
            'motor_profiles_json': profiles
        }
    except Exception as e:
        print(f"Error loading profiles for templates: {e}")
        return {
            'MOTOR_PROFILES': {},
            'motor_profiles_json': {}
        }

if __name__ == '__main__':
    # threading.Timer(1.5, open_browser).start()
    app.run(debug=True, use_reloader=False)
    t=1
