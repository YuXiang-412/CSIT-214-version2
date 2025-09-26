async function loadFlights(){
  const r = await fetch('/api/flights');
  const j = await r.json();
  if(!j.ok) return;
  const flights = j.flights;
  const coefs = j.cabin_price_coefs || {Y:1.0, J:1.5, F:2.0};
  const c = document.getElementById('flights');
  c.innerHTML = flights.map(f=>`<div class="card">
    <p><strong>${f.id}</strong> ${f.from} → ${f.to} | <span id="price_${f.id}">$${(f.price*coefs['Y']).toFixed(2)}</span> | ${f.base_miles} miles</p>
    <p>
      Cabin: <select id="cabin_${f.id}"><option value="Y">Economy</option><option value="J">Business</option><option value="F">First</option></select>
      <button onclick="buy('${f.id}')">Buy</button>
    </p>
  </div>`).join('');
  // attach change handlers to update price display
  flights.forEach(f=>{
    const sel = document.getElementById('cabin_'+f.id);
    sel.addEventListener('change', ()=>{
      const coef = coefs[sel.value] || 1.0;
      const priceSpan = document.getElementById('price_'+f.id);
      priceSpan.textContent = '$'+(f.price*coef).toFixed(2);
    });
  });
}
async function buy(flightId){
  const cabin = document.getElementById('cabin_'+flightId).value;
  const res = await fetch('/api/buy', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({flight_id: flightId, cabin})});
  const j = await res.json();
  alert(JSON.stringify(j));
  if(j.ok) loadFlights();
}
window.onload = loadFlights;
