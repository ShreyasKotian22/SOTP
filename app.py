from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
import psycopg2
from psycopg2.extras import RealDictCursor
import random
import smtplib
import hashlib
import tempfile
import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font
from datetime import datetime
import os

# Optional email config import
try:
    from email_config import EMAIL_HOST, EMAIL_PORT, EMAIL_USERNAME, EMAIL_PASSWORD
except ImportError:
    EMAIL_HOST = EMAIL_PORT = EMAIL_USERNAME = EMAIL_PASSWORD = None

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# === PostgreSQL Configs ===
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:shreyas@localhost/login_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

DB_CONFIG = {
    'dbname': 'login_db',
    'user': 'postgres',
    'password': 'shreyas',
    'host': 'localhost',
    'port': '5432'
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

# === Models ===
class User(db.Model):
    __tablename__ = 'users'
    email = db.Column(db.String(100), primary_key=True)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False)

# === Utilities ===
verification_codes = {}

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def send_email(to_email, subject, body):
    try:
        message = f"Subject: {subject}\n\n{body}"
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USERNAME, to_email, message)
    except Exception as e:
        print(f"Email error: {e}")
        raise

def get_orders():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""SELECT id, client_name, company_name, project_name, po_amount FROM orders""")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{
        'order_id': row[0],
        'client_name': row[1],
        'company': row[2],
        'project': row[3],
        'po_amount': row[4],
        'profit': "₹0.00"
    } for row in rows]

# === New: Chart Data Endpoint ===
@app.route('/api/chart-data')
def chart_data():
    from_date = request.args.get('from')  # format: YYYY-MM-DD
    to_date = request.args.get('to')      # format: YYYY-MM-DD

    conn = get_db_connection()
    cur = conn.cursor()

    # Base SQL
    query = """
        SELECT
            end_date,
            profit_percentage,
            po_amount
        FROM orders
        WHERE end_date IS NOT NULL
          AND profit_percentage IS NOT NULL
          AND po_amount IS NOT NULL
    """

    # Add filter conditionally
    params = []
    if from_date and to_date:
        query += " AND end_date BETWEEN %s AND %s"
        params.extend([from_date, to_date])

    query += " ORDER BY end_date"

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    end_dates = [r[0].strftime('%Y-%m-%d') for r in rows]
    percentages = [float(r[1]) for r in rows]
    po_amounts = [float(r[2]) for r in rows]

    return jsonify({
        'end_dates': end_dates,
        'percentages': percentages,
        'po_amounts': po_amounts
    })

# === Auth Routes ===
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email'].strip().lower()
    password = request.form['password'].strip()
    hashed_password = hash_password(password)
    user = User.query.filter(func.lower(User.email)==email, User.password==hashed_password).first()
    if user:
        session['user'] = user.email
        session['role'] = user.role
        return redirect(url_for('dashboard'))
    flash('Invalid Credentials!', 'danger')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash('Please log in first', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM clients")
    total_clients = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders")
    total_orders = cur.fetchone()[0]

    cur.execute("SELECT AVG(profit_percentage) FROM orders WHERE profit_percentage IS NOT NULL")
    avg_profit = cur.fetchone()[0] or 0

    cur.close()
    conn.close()

    hour = datetime.now().hour
    greeting = (
        "Good Morning," if 5 <= hour < 12 else
        "Good Afternoon," if 12 <= hour < 17 else
        "Good Evening," if 17 <= hour < 21 else
        "Hello,"
    )

    return render_template(
        'dashboard.html',
        email=session['user'],
        role=session['role'],
        greeting=greeting,
        total_clients=total_clients,
        total_orders=total_orders,
        average_profit=round(avg_profit, 2)
    )

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))



# === Forgot & Reset Password ===
@app.route('/forgot-password')
def forgot_password():
    return render_template('forgot.html')

@app.route('/send-code', methods=['POST'])
def send_code():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    user = User.query.filter(func.lower(User.email)==email).first()
    if not user:
        return jsonify({'success': False, 'message': 'Email not registered.'})
    code = str(random.randint(100000, 999999))
    verification_codes[email] = code
    send_email(email, "Your Password Reset Code", f"Your code is: {code}")
    return jsonify({'success': True, 'message': 'Verification code sent.'})

@app.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    code = data.get('code','').strip()
    if verification_codes.get(email)!=code:
        return jsonify({'success': False, 'message': 'Invalid or missing code.'})
    verification_codes.pop(email, None)
    return jsonify({'success': True, 'message': 'Code verified.'})

@app.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    new_password = data.get('new_password','').strip()
    user = User.query.filter(func.lower(User.email)==email).first()
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'})
    user.password = hash_password(new_password)
    db.session.commit()
    verification_codes.pop(email, None)
    return jsonify({'success': True, 'message': 'Password reset successful.'})

# === Order Management ===
@app.route('/view-orders')
def view_orders():
    if 'user' not in session:
        flash('Please log in', 'danger')
        return redirect(url_for('index'))
    return render_template('view_sales_orders.html')

@app.route('/api/orders')
def api_orders():
    return jsonify(get_orders())

@app.route('/api/delete_order/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM orders WHERE id=%s", (order_id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"message":"Order deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download/order/<int:order_id>')
def download_order_excel(order_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, client_name, division, company_name, project_name, sub_contractor_name,
               start_date, end_date, site, po_number, po_amount, issued_by, po_date, profit_percentage
        FROM orders WHERE id=%s
    """, (order_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return "Order not found", 404

    columns = [
        "ID", "Client Name", "Division", "Company Name", "Project Name", "Sub Contractor Name",
        "Start Date", "End Date", "Site", "PO Number", "PO Amount", "Issued By", "PO Date", "Profit Percentage",
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Order {order_id}"

    for i, h in enumerate(columns, 1):
        cell = ws.cell(row=1, column=i, value=h)  # ✅ corrected
        cell.font = Font(bold=True)

    for i, val in enumerate(row, 1):
        ws.cell(row=2, column=i, value=val)  # ✅ corrected

    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value), default=0)
        ws.column_dimensions[col[0].column_letter].width = length + 2

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    wb.save(tmp.name)

    return send_file(tmp.name, as_attachment=True, download_name=f'Order_{order_id}.xlsx')

@app.route('/add-order')
def add_order_form():
    return render_template('add.html')

@app.route('/submit', methods=['POST'])
def submit_order():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid data"}), 400
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                client_name TEXT,
                division TEXT,
                company_name TEXT,
                project_name TEXT,
                sub_contractor_name TEXT,
                start_date DATE,
                end_date DATE,
                site TEXT,
                po_number TEXT,
                po_amount TEXT,
                issued_by TEXT,
                po_date DATE
            );
        ''')
        cur.execute('''
            INSERT INTO orders (
                client_name, division, company_name, project_name, sub_contractor_name,
                start_date, end_date, site, po_number, po_amount, issued_by, po_date
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        ''', (
            data.get('clientName'),
            data.get('division'),
            data.get('companyName'),
            data.get('projectName'),
            data.get('subContractorName'),
            data.get('startDate'),
            data.get('endDate'),
            data.get('site'),
            data.get('poNumber'),
            data.get('poAmount'),
            data.get('issuedBy'),
            data.get('poDate')
        ))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
# === Client Management ===
@app.route('/clients')
def clients():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, client_name, company_name, email, gst_no, billing_address FROM clients")
    clients = cur.fetchall()
    cur.close(); conn.close()
    return render_template('addclients.html', clients=clients)

@app.route('/delete-client/<int:client_id>', methods=['DELETE'])
def delete_client(client_id):
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM clients WHERE id=%s", (client_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success":True})
    except Exception as e:
        return jsonify({"success":False, "error":str(e)})

@app.route('/add-client', methods=['POST'])
def add_client():
    data = request.get_json()
    try:
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO clients (client_name, company_name, email, phone, billing_address, gst_no)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (data['client_name'],data['company_name'],data['email'],
              data['phone'],data['billing_address'],data['gst_no']))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"message":"Client added successfully!"})
    except Exception as e:
        return jsonify({"error":str(e)}),400

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True)
