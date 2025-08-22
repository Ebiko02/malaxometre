{% extends 'base.html' %}
{% block content %}
<style>
  .page-wrap{max-width:920px;margin:28px auto;padding:20px}
  h2{font-size:1.6rem;margin-bottom:6px;color:#07123b}
  p.lead{color:#55607a;margin:0 0 14px 0}
  form.card{
    background:#fff;border-radius:12px;padding:18px;box-shadow:0 8px 24px rgba(8,20,40,0.06);
  }
  .row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}
  .col{flex:1 1 200px;min-width:160px}
  label{display:block;font-weight:700;color:#0f172a;margin-bottom:6px;font-size:0.95rem}
  input[type="number"]{
    width:100%;padding:12px 14px;border-radius:10px;border:1px solid #e6eef8;font-size:1rem;
    min-height:48px;box-sizing:border-box;
  }

/* New: hide native spinner controls and add touch-friendly stepper */
input[type=number]::-webkit-outer-spin-button,
input[type=number]::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type=number] {
  -moz-appearance: textfield;
}

/* Stepper layout */
.stepper{display:flex;align-items:center;gap:8px}
.step-btn{
  display:inline-flex;align-items:center;justify-content:center;
  width:50px;height:44px;border-radius:10px;border:1px solid #d7e3ff;
  background:#fff;font-size:20px;color:#0b5ed7;font-weight:700;
  -webkit-tap-highlight-color: rgba(0,0,0,0.06);
  box-shadow:0 4px 8px rgba(11,94,215,0.06);
  touch-action: manipulation;
}
.step-btn:active{transform:translateY(1px)}
/* make sure input keeps a large target */
.stepper input[type="number"]{flex:1;min-height:44px;padding:10px 12px}

  .flashes{margin-top:12px;display:flex;flex-direction:column;gap:8px}
  .flash.success{background:#e6ffed;border:1px solid #d1f6dc;padding:10px;border-radius:8px;color:#0b9451}
  .flash.error{background:#fff1f0;border:1px solid #ffd6d8;padding:10px;border-radius:8px;color:#b32222}
  @media (max-width:640px){
    .actions{justify-content:stretch}
    .row{flex-direction:column}
  }
</style>

<div class="page-wrap">
  <h2>Créer un profil personnalisé</h2>
  <p class="lead">Ajoutez autant de phases que nécessaire. Chaque phase définit une fréquence (Hz) et une durée (s).</p>

  <form class="card" method="post" novalidate>
    <div class="row" style="align-items:end;">
      <div class="col" style="flex:0 0 160px;">
        <label for="num_phases">Nombre de phases</label>
        <!-- removed max attribute to allow unlimited phases -->
        <input id="num_phases" name="num_phases" type="number" min="1" value="1" inputmode="numeric" aria-label="Nombre de phases">
      </div>
      <div class="col" style="flex:1 1 auto">
        <label style="visibility:hidden">placeholder</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button type="button" class="big_button ghost" id="addPhaseBtn" aria-label="Ajouter une phase">Ajouter</button>
          <button type="button" class="big_button ghost" id="removePhaseBtn" aria-label="Supprimer la dernière phase">Supprimer</button>
        </div>
      </div>
    </div>

    <div id="phases" aria-live="polite"></div>

    <div class="helper">Astuce : pour stopper le moteur, créez une phase avec fréquence 0.</div>

    <div class="flashes" id="flash_container" aria-live="assertive">
      {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
          {% for category, message in messages %}
            <div class="flash {{ 'success' if category in ['success','ok'] else 'error' }}">{{ message }}</div>
          {% endfor %}
        {% endif %}
      {% endwith %}
    </div>

    <div class="actions">
      <a href="{{ url_for('profiles') }}" class="big_button ghost" style="display:inline-flex;align-items:center;justify-content:center;text-decoration:none;">Annuler</a>
      <button type="submit" class="big_button">Lancer le profil</button>
    </div>
  </form>
</div>

<script>
(function(){
  const numInput = document.getElementById('num_phases');
  const phasesEl = document.getElementById('phases');
  const addBtn = document.getElementById('addPhaseBtn');
  const removeBtn = document.getElementById('removePhaseBtn');

  function renderPhases(){
    let num = parseInt(numInput.value) || 1;
    num = Math.max(1, num); // no upper cap
    numInput.value = num;
    let html = '';
    for(let i=0;i<num;i++){
      html += `
        <fieldset class="phase" aria-label="Phase ${i+1}">
          <legend>Phase ${i+1}</legend>
          <div class="row">
            <div class="col">
              <label for="freq_${i}">Fréquence (Hz)</label>
              <div class="stepper">
                <button class="step-btn" type="button" data-action="dec" data-target="freq_${i}">−</button>
                <input id="freq_${i}" name="freq_${i}" type="number" min="0" max="100" step="1" inputmode="numeric" required aria-required="true" value="${i===0?10:0}">
                <button class="step-btn" type="button" data-action="inc" data-target="freq_${i}">+</button>
              </div>
            </div>
            <div class="col" style="flex:0 0 140px;">
              <label for="seconds_${i}">Durée (s)</label>
              <div class="stepper">
                <button class="step-btn" type="button" data-action="dec" data-target="seconds_${i}">−</button>
                <input id="seconds_${i}" name="seconds_${i}" type="number" min="1" max="3600" inputmode="numeric" required aria-required="true" value="${i===0?10:5}">
                <button class="step-btn" type="button" data-action="inc" data-target="seconds_${i}">+</button>
              </div>
            </div>
          </div>
        </fieldset>
      `;
    }
    phasesEl.innerHTML = html;
    const firstInput = phasesEl.querySelector('input');
    if(firstInput) firstInput.focus();
  }

  // handle stepper clicks with event delegation
  phasesEl.addEventListener('click', function(e){
    const btn = e.target.closest('.step-btn');
    if(!btn) return;
    const action = btn.dataset.action;
    const targetId = btn.dataset.target;
    const input = document.getElementById(targetId);
    if(!input) return;
    const step = parseFloat(input.step || 1) || 1;
    const min = (input.min !== '') ? parseFloat(input.min) : -Infinity;
    const max = (input.max !== '') ? parseFloat(input.max) : Infinity;
    let val = parseFloat(input.value) || 0;
    if(action === 'inc') val = Math.min(max, val + step);
    else if(action === 'dec') val = Math.max(min, val - step);
    input.value = (Number.isInteger(step)) ? Math.round(val) : val.toFixed(2);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // Controls
  numInput.addEventListener('change', () => renderPhases());
  addBtn.addEventListener('click', () => {
    let v = (parseInt(numInput.value)||1) + 1; // no upper limit
    numInput.value = v; renderPhases();
  });
  removeBtn.addEventListener('click', () => {
    let v = Math.max(1, (parseInt(numInput.value)||1) - 1);
    numInput.value = v; renderPhases();
  });

  // initial render
  window.addEventListener('load', renderPhases);

  // Improve form submission on touch: ensure values present
  document.querySelector('form.card').addEventListener('submit', function(e){
    const inputs = this.querySelectorAll('input[type="number"][required]');
    for(const inp of inputs){
      if(!inp.value){
        inp.focus();
        e.preventDefault();
        return false;
      }
    }
    return true;
  });
})();
</script>
{% endblock %}
