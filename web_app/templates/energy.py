{% extends 'base.html' %}
{% block content %}
<style>
  .energy-wrap{max-width:920px;margin:28px auto;padding:20px;display:flex;gap:18px;flex-wrap:wrap;align-items:stretch;justify-content:space-between}
  .card{flex:1 1 620px;background:linear-gradient(180deg,#ffffff,#f7fbff);border-radius:12px;padding:20px;box-shadow:0 8px 24px rgba(6,24,48,0.06);border:1px solid rgba(15,23,42,0.04)}
  h1,h2{margin:0 0 8px 0;color:#07123b}
  p.lead{color:#55607a;margin:0 0 12px}
  .controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-top:12px}
  .big_button{background:#0b5ed7;color:#fff;border:none;padding:12px 18px;border-radius:12px;font-weight:700;cursor:pointer;min-height:48px;box-shadow:0 6px 18px rgba(11,94,215,0.12)}
  .big_button.secondary{background:#475569}
  label{display:block;font-weight:700;color:#0f172a;margin-bottom:6px;font-size:0.95rem}
  input[type=number]{width:100%;padding:12px;border-radius:10px;border:1px solid #e6eef8;min-height:48px;font-size:1rem;box-sizing:border-box}
  .result{margin-top:18px;padding:14px;border-radius:10px;background:#fff;border:1px solid #eef6ff;text-align:center}
  .value{font-size:2.8rem;font-weight:800;color:#0b5ed7}
  .unit{font-size:1rem;color:#334155;font-weight:700}
  .note{margin-top:12px;color:#6b7280;font-size:0.95rem;text-align:center}

  /* hide native number spinners */
  input[type=number]::-webkit-outer-spin-button,
  input[type=number]::-webkit-inner-spin-button { -webkit-appearance:none; margin:0; }
  input[type=number]{ -moz-appearance:textfield; }

  /* Touch-friendly stepper */
  .stepper{display:flex;align-items:center;gap:10px}
  .step-btn{
    display:inline-flex;align-items:center;justify-content:center;
    width:64px;height:56px;border-radius:12px;border:1px solid #d7e3ff;
    background:#fff;font-size:28px;color:#0b5ed7;font-weight:800;
    -webkit-tap-highlight-color: rgba(0,0,0,0.06);
    box-shadow:0 6px 16px rgba(11,94,215,0.06);
    touch-action: manipulation;
  }
  .step-btn:active{transform:translateY(1px)}
  .step-input{flex:1;min-height:56px;padding:12px 14px;border-radius:10px;border:1px solid #e6eef8;font-size:1.25rem;text-align:center}

  @media (max-width:780px){ .energy-wrap{padding:12px} .value{font-size:2rem} .controls{flex-direction:column;align-items:stretch} .step-btn{width:56px;height:52px;font-size:24px} .step-input{min-height:52px} }
</style>

<div class="energy-wrap">
  <div class="card" role="region" aria-labelledby="noLoadTitle">
    <h2 id="noLoadTitle">Mesure puissance à vide</h2>
    <p class="lead">Mesurez la puissance consommée par le moteur sans charge. Choisissez la fréquence et la durée de mesure.</p>

    <form method="post" action="{{ url_for('no_load_power') }}" style="margin-top:12px;">
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center;">
        <div style="flex:1 1 220px;min-width:160px;">
          <label for="freq">Fréquence (Hz)</label>
          <div class="stepper" aria-label="Commande fréquence">
            <button type="button" class="step-btn" data-action="dec" data-target="freq_input" aria-label="Diminuer fréquence">−</button>
            <input id="freq_input" name="frequency" class="step-input" type="number" min="0" max="100" step="1" value="{{ frequency|default(10) }}" inputmode="numeric" required aria-required="true">
            <button type="button" class="step-btn" data-action="inc" data-target="freq_input" aria-label="Augmenter fréquence">+</button>
          </div>
        </div>

        <div style="flex:1 1 160px;min-width:120px;">
          <label for="dur">Durée (s)</label>
          <div class="stepper" aria-label="Commande durée">
            <button type="button" class="step-btn" data-action="dec" data-target="dur_input" aria-label="Diminuer durée">−</button>
            <input id="dur_input" name="duration" class="step-input" type="number" min="1" max="3600" step="1" value="{{ duration|default(30) }}" inputmode="numeric" required aria-required="true">
            <button type="button" class="step-btn" data-action="inc" data-target="dur_input" aria-label="Augmenter durée">+</button>
          </div>
        </div>
      </div>

      <div class="controls">
        <button type="submit" class="big_button">Démarrer la mesure</button>
        <a href="{{ url_for('profiles') }}" class="big_button secondary" style="display:inline-flex;align-items:center;justify-content:center;text-decoration:none;">Retour profils</a>
      </div>
    </form>

    {% if (power_W is defined and power_W is not none) or (energy_Wh is defined and energy_Wh is not none) %}
      <div class="result" aria-live="polite">
        {% if power_W is defined and power_W is not none %}
          <div style="margin-bottom:6px;color:#55607a;font-weight:700">Puissance moyenne (à vide)</div>
          <div><span class="value">{{ '%.1f'|format(power_W) }}</span> <span class="unit">W</span></div>
        {% endif %}
        {% if energy_Wh is defined and energy_Wh is not none %}
          <div style="margin-top:10px;color:#55607a;font-weight:700">Énergie mesurée</div>
          <div><span class="value">{{ '%.3f'|format(energy_Wh) }}</span> <span class="unit">Wh</span></div>
        {% endif %}
        {% if measured_at is defined and measured_at is not none %}
          <div style="margin-top:8px;color:#6b7280;font-size:0.95rem">Mesure effectuée : {{ measured_at }}</div>
        {% endif %}
      </div>
    {% endif %}

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <div style="margin-top:12px;">
          {% for category, message in messages %}
            <div style="padding:10px;border-radius:8px;margin-bottom:8px;background:#fff8dc;border:1px solid #f0e6b6;color:#0f172a;">{{ message }}</div>
          {% endfor %}
        </div>
      {% endif %}
    {% endwith %}

    <div class="note">Astuce : pour une lecture stable, laissez le moteur se stabiliser quelques secondes avant la mesure.</div>
  </div>

  <aside style="flex:0 0 300px;background:#fff;border-radius:12px;padding:16px;border:1px solid #eef2ff;box-shadow:0 6px 18px rgba(2,6,23,0.04)">
    <h3 style="margin:0 0 8px 0;color:#0f172a">Informations</h3>
    <ul style="color:#475569;margin:8px 0 0 18px;padding:0;font-size:0.95rem">
      <li>PF utilisé : {{ pf|default('auto') }}</li>
      <li>Puissance apparente S = V × I × √3 (trois‑phases).</li>
      <li>Vérifiez les échelles Modbus (V, I) si les valeurs semblent incorrectes.</li>
    </ul>
  </aside>
</div>

<script>
document.addEventListener('click', function(e){
  const btn = e.target.closest('.step-btn');
  if(!btn) return;
  const action = btn.dataset.action;
  const targetId = btn.dataset.target;
  const input = document.getElementById(targetId);
  if(!input) return;
  const step = Math.abs(parseFloat(input.step || 1)) || 1;
  const min = (input.min !== '') ? parseFloat(input.min) : -Infinity;
  const max = (input.max !== '') ? parseFloat(input.max) : Infinity;
  let val = parseFloat(input.value) || 0;
  if(action === 'inc') val = Math.min(max, val + step);
  else if(action === 'dec') val = Math.max(min, val - step);
  input.value = Number.isInteger(step) ? Math.round(val) : parseFloat(val.toFixed(2));
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
</script>
{% endblock %}
