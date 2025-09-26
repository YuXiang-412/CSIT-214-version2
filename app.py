from flask import Flask, request, jsonify, render_template, redirect, url_for, session
import json, threading, os, datetime
from pathlib import Path

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = 'replace-this-with-a-random-secret-in-prod'

DATA_PATH = Path(__file__).parent / "data.json"
lock = threading.Lock()

def now_ts():
    return datetime.datetime.now().isoformat(timespec='seconds')

def read_data():
    with lock:
        with open(DATA_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)

def write_data(data):
    with lock:
        backup = DATA_PATH.with_suffix('.bak.json')
        with open(backup, 'w', encoding='utf-8') as bf:
            json.dump(data, bf, indent=2, ensure_ascii=False)
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login_page'))
        return fn(*args, **kwargs)
    return wrapper

@app.route('/')
def home():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET','POST'])
def login_page():
    if request.method == 'GET':
        return render_template('login.html')
    data = request.form
    username = data.get('username')
    password = data.get('password')
    d = read_data()
    user = d['users'].get(username)
    if not user or user.get('password') != password:
        return render_template('login.html', error='Invalid credentials')
    session['username'] = username
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/buy')
@login_required
def buy_page():
    return render_template('buy.html')

@app.route('/redeem')
@login_required
def redeem_page():
    return render_template('redeem.html')

@app.route('/status')
@login_required
def status_page():
    return render_template('status.html')

@app.route('/history')
@login_required
def history_page():
    return render_template('history.html')

@app.route('/api/me')
def api_me():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    d = read_data()
    u = d['users'].get(session['username'])
    safe = {k: u[k] for k in ('balance','points_balance','status','tier_points_year','tier_segments_year') if k in u}
    safe['username'] = session['username']
    return jsonify({'ok': True, 'user': safe})

@app.route('/api/flights')
def api_flights():
    d = read_data()
    # expose cabin price coefficients so UI can show prices per cabin class
    cabin_price_coefs = d.get('cabin_price_coefs', {'Y': 1.0, 'J': 1.5, 'F': 2.0})
    return jsonify({'ok': True, 'flights': d.get('flights', []), 'cabin_price_coefs': cabin_price_coefs})

def compute_earned_points(d, base_miles, cabin_class, user_status):
    cabin_coef = d['flight_earnings_rules']['cabin_coefs'].get(cabin_class,1.0)
    tier_coef = d['flight_earnings_rules']['tier_coefs'].get(user_status,1.0)
    pts = int(round(base_miles * cabin_coef * tier_coef))
    return pts

@app.route('/api/buy', methods=['POST'])
def api_buy():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    payload = request.get_json() or {}
    flight_id = payload.get('flight_id')
    cabin = payload.get('cabin', 'Y')
    d = read_data()
    user = d['users'].get(session['username'])
    flight = next((f for f in d['flights'] if f['id']==flight_id), None)
    if not flight:
        return jsonify({'ok': False, 'error': 'flight_not_found'}), 404
    # compute actual price based on cabin selection
    base_price = flight.get('price', 0.0)
    cabin_price_coefs = d.get('cabin_price_coefs', {'Y':1.0,'J':1.5,'F':2.0})
    coef = cabin_price_coefs.get(cabin, 1.0)
    price = round(base_price * coef, 2)
    if user.get('balance',0) < price:
        return jsonify({'ok': False, 'error': 'insufficient_balance'}), 400
    user['balance'] -= price
    earned = compute_earned_points(d, flight['base_miles'], cabin, user['status'])
    user['points_balance'] = user.get('points_balance',0) + earned
    tier_pts = int(flight['base_miles'] / 100)
    user['tier_points_year'] = user.get('tier_points_year',0) + tier_pts
    user['tier_segments_year'] = user.get('tier_segments_year',0) + 1
    ticket = {'flight_id': flight_id, 'price': price, 'cabin': cabin, 'miles': flight['base_miles'], 'date': now_ts(), 'type':'paid', 'earned': earned}
    user.setdefault('tickets', []).append(ticket)
    user.setdefault('history', []).append({'ts': now_ts(), 'type':'earn', 'details': f'Bought {flight_id}, earned {earned} pts'})
    d.setdefault('audit', []).append({'ts': now_ts(), 'user': session['username'], 'action':'buy', 'flight_id': flight_id, 'earned': earned})
    thresholds = d.get('status_thresholds', {})
    old_status = user.get('status','Base')
    new_status = old_status
    for st in ['Gold','Silver']:
        thr = thresholds.get(st, {})
        if user.get('tier_points_year',0) >= thr.get('tier_points', float('inf')) or user.get('tier_segments_year',0) >= thr.get('tier_segments', float('inf')):
            new_status = st
            break
    if new_status != old_status:
        user['status'] = new_status
        user.setdefault('history', []).append({'ts': now_ts(), 'type':'upgrade', 'details': f'Auto-upgraded to {new_status}'})
        d.setdefault('audit', []).append({'ts': now_ts(), 'user': session['username'], 'action':'auto_upgrade', 'new_status': new_status})
    write_data(d)
    return jsonify({'ok': True, 'earned': earned, 'new_points': user['points_balance'], 'balance': user['balance'], 'upgraded': new_status!=old_status, 'new_status': user['status']})

@app.route('/api/gifts')
def api_gifts():
    d = read_data()
    return jsonify({'ok': True, 'gifts': d.get('gifts', [])})

@app.route('/api/redeem_gift', methods=['POST'])
def api_redeem_gift():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    payload = request.get_json() or {}
    gift_id = payload.get('gift_id')
    d = read_data()
    gift = next((g for g in d.get('gifts',[]) if g['id']==gift_id), None)
    if not gift:
        return jsonify({'ok': False, 'error': 'gift_not_found'}), 404
    user = d['users'].get(session['username'])
    cost = gift['cost_points']
    if user.get('points_balance',0) < cost:
        return jsonify({'ok': False, 'error': 'insufficient_points'}), 400
    user['points_balance'] -= cost
    user.setdefault('redemptions', []).append({'ts': now_ts(), 'type':'gift', 'gift_id': gift_id, 'name': gift['name'], 'points': cost})
    user.setdefault('history', []).append({'ts': now_ts(), 'type':'spend', 'details': f'Redeemed gift {gift["name"]} (-{cost} pts)'})
    d.setdefault('audit', []).append({'ts': now_ts(), 'user': session['username'], 'action':'redeem_gift', 'gift_id': gift_id, 'points': cost})
    write_data(d)
    return jsonify({'ok': True, 'spent': cost, 'new_points': user['points_balance']})

@app.route('/api/redeem_discount', methods=['POST'])
def api_redeem_discount():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    payload = request.get_json() or {}
    points = int(payload.get('points',0))
    d = read_data()
    rates = d.get('discount_rates', {})
    user = d['users'].get(session['username'])
    if str(points) not in rates:
        return jsonify({'ok': False, 'error': 'invalid_option'}), 400
    if user.get('points_balance',0) < points:
        return jsonify({'ok': False, 'error': 'insufficient_points'}), 400
    discount = rates[str(points)]
    user['points_balance'] -= points
    user['balance'] += discount
    user.setdefault('history', []).append({'ts': now_ts(), 'type':'spend', 'details': f'Converted {points} pts to ${discount} credit'})
    d.setdefault('audit', []).append({'ts': now_ts(), 'user': session['username'], 'action':'redeem_discount', 'points': points, 'credit': discount})
    write_data(d)
    return jsonify({'ok': True, 'credit': discount, 'new_balance': user['balance'], 'new_points': user['points_balance']})

@app.route('/api/upgrade_status', methods=['POST'])
def api_upgrade_status():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    payload = request.get_json() or {}
    target = payload.get('target')
    d = read_data()
    user = d['users'].get(session['username'])
    cost_map = d.get('upgrade_cost_points', {})
    if target not in cost_map:
        return jsonify({'ok': False, 'error': 'invalid_target'}), 400
    cost = cost_map[target]
    if user.get('points_balance',0) < cost:
        return jsonify({'ok': False, 'error': 'insufficient_points'}), 400
    user['points_balance'] -= cost
    old = user.get('status','Base')
    user['status'] = target
    user.setdefault('history', []).append({'ts': now_ts(), 'type':'upgrade', 'details': f'Paid {cost} pts to upgrade from {old} to {target}'})
    d.setdefault('audit', []).append({'ts': now_ts(), 'user': session['username'], 'action':'paid_upgrade', 'from': old, 'to': target, 'points': cost})
    write_data(d)
    return jsonify({'ok': True, 'new_status': user['status'], 'new_points': user['points_balance']})

@app.route('/api/history')
def api_history():
    if 'username' not in session:
        return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
    d = read_data()
    user = d['users'].get(session['username'])
    return jsonify({'ok': True, 'history': user.get('history', []), 'tickets': user.get('tickets', []), 'redemptions': user.get('redemptions', [])})

@app.route('/api/gifts_and_discounts')
def api_gifts_and_discounts():
    d = read_data()
    return jsonify({'ok': True, 'gifts': d.get('gifts', []), 'discounts': d.get('discount_rates', {}), 'upgrade_costs': d.get('upgrade_cost_points', {})})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
