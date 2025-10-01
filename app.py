from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
import sqlite3, os
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(BASE_DIR, 'instance', 'nexline.db')
os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT
    )''')
    conn.commit()

    # Add demo users if not present
    cur.execute("SELECT username FROM users WHERE username='demo_manager'")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                    ('demo_manager', generate_password_hash('demo123'), 'manager'))

    cur.execute("SELECT username FROM users WHERE username='demo_maint'")
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                    ('demo_maint', generate_password_hash('maint123'), 'maintenance'))

    conn.commit()
    conn.close()

app = Flask(__name__)
app.secret_key = 'dev-secret-key'

@app.before_first_request
def setup():
    init_db()

# ✅ FIXED FUNCTION
def read_csv(name):
    path = os.path.join(DATA_DIR, name)
    df = pd.read_csv(path)
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'static'), filename)

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    plants = read_csv('plants_summary.csv').to_dict('records')
    return render_template('index.html', plants=plants)

@app.route('/about')
def about():
    if 'user' not in session:
        return redirect(url_for('login'))
    master = read_csv('machines_master.csv').to_dict('records')
    return render_template('about.html', machines=master)

@app.route('/all-machines')
def all_machines():
    if 'user' not in session:
        return redirect(url_for('login'))
    df = read_csv('machines_data.csv')
    master = read_csv('machines_master.csv').set_index('machine_id')
    now = df['timestamp'].max()
    cutoff = now - pd.Timedelta(hours=24)

    machines = []
    for mid in master.index:
        sub = df[df['machine_id'] == mid]
        if sub.empty: 
            continue
        last = sub.sort_values('timestamp').iloc[-1]
        last24 = sub[sub['timestamp'] >= cutoff]
        uptime_pct = round((last24['uptime_seconds'].sum() / (len(last24)*300))*100, 2) if len(last24) > 0 else 0
        machines.append({
            'machine_id': mid,
            'plant': master.loc[mid, 'plant'],
            'production_line': master.loc[mid, 'production_line'],
            'machine_type': master.loc[mid, 'machine_type'],
            'current_status': last['status'],
            'uptime_pct_24h': uptime_pct,
            'total_runtime_hours': last['total_runtime_hours'],
            'last_downtime': str(sub[sub['status']=='Down']['timestamp'].max()) if (sub['status']=='Down').any() else ''
        })

    plants = read_csv('plants_summary.csv').to_dict('records')
    return render_template('all_machines.html', machines=machines, plants=plants)

@app.route('/lookup', methods=['GET','POST'])
def lookup():
    if 'user' not in session:
        return redirect(url_for('login'))
    master = read_csv('machines_master.csv').to_dict('records')
    if request.method == 'POST':
        mid = request.form.get('machine_id')
        return redirect(url_for('machine_detail', machine_id=mid))
    return render_template('lookup.html', machines=master)

@app.route('/machine/<machine_id>')
def machine_detail(machine_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    df = read_csv('machines_data.csv')
    sub = df[df['machine_id'] == machine_id].copy()
    if sub.empty:
        return render_template('machine_not_found.html', machine_id=machine_id)

    sub = sub.sort_values('timestamp')
    current = sub.iloc[-1]
    now = df['timestamp'].max()
    cutoff = now - pd.Timedelta(hours=48)
    last48 = sub[sub['timestamp'] >= cutoff]

    uptime_pct = round((last48['uptime_seconds'].sum() / (len(last48)*300))*100, 2) if len(last48) > 0 else 0
    total_runtime = current['total_runtime_hours']

    master = read_csv('machines_master.csv').set_index('machine_id')
    expected = master.loc[machine_id, 'expected_life_hours'] if machine_id in master.index else 50000
    remaining = expected - total_runtime

    chart_ts = last48['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S').tolist()
    chart_running = (last48['uptime_seconds'] > 0).astype(int).tolist()
    chart_runtime = last48['total_runtime_hours'].tolist()
    events = sub.tail(20).to_dict('records')[::-1]

    return render_template('machine.html', machine_id=machine_id,
                           machine_type=current['machine_type'],
                           production_line=current['production_line'],
                           plant=current['plant'],
                           current_status=current['status'],
                           uptime_pct=uptime_pct,
                           total_runtime=total_runtime,
                           expected_life=expected,
                           remaining=remaining,
                           chart_ts=chart_ts,
                           chart_running=chart_running,
                           chart_runtime=chart_runtime,
                           events=events)

@app.route('/contact')
def contact():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('contact.html')

@app.route('/admin')
def admin():
    if 'user' not in session:
        return redirect(url_for('login'))
    docs = ['machines_data.xlsx','machines_master.xlsx','plants_summary.xlsx','events_log.xlsx','users_demo.xlsx']
    return render_template('admin.html', docs=docs)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row['password_hash'], password):
            session['user'] = username
            session['role'] = row['role']
            return redirect(url_for('index'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form.get('role','viewer')
        pw_hash = generate_password_hash(password)

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                        (username, pw_hash, role))
            conn.commit()
            flash('Registered. Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception:
            flash('Username taken', 'danger')
        finally:
            conn.close()
    return render_template('register.html')

if __name__ == '__main__':
    app.run(debug=True)
