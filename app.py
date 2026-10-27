import os
from functools import wraps
from io import BytesIO

import mysql.connector
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key-for-submission")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),  # Set this if your MySQL root user has a password
    "database": os.environ.get("DB_NAME", "sales_management"),
}

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def ensure_demo_user():
    """Create the demo admin account if it does not exist."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username=%s", ("admin",))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
            ("admin", generate_password_hash("admin123"))
        )
        conn.commit()
    cur.close()
    conn.close()

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS count FROM products")
    product_count = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) AS count FROM customers")
    customer_count = cur.fetchone()["count"]
    cur.execute("SELECT COUNT(*) AS count FROM sales")
    sales_count = cur.fetchone()["count"]
    cur.execute("SELECT COALESCE(SUM(total_amount),0) AS total FROM sales")
    total_sales = cur.fetchone()["total"]
    cur.execute("""SELECT DATE_FORMAT(sale_date, '%Y-%m') AS month,
                          SUM(total_amount) AS total
                   FROM sales GROUP BY DATE_FORMAT(sale_date, '%Y-%m')
                   ORDER BY month DESC LIMIT 6""")
    monthly = list(reversed(cur.fetchall()))
    cur.execute("""SELECT s.id, s.sale_date, s.total_amount,
                          COALESCE(c.name, 'Walk-in Customer') AS customer
                   FROM sales s LEFT JOIN customers c ON s.customer_id=c.id
                   ORDER BY s.sale_date DESC LIMIT 5""")
    recent_sales = cur.fetchall()
    cur.execute("SELECT id, name, stock, price FROM products ORDER BY stock ASC, name ASC LIMIT 5")
    stock_items = cur.fetchall()
    cur.execute("""SELECT product_name, SUM(quantity) AS units, SUM(line_total) AS revenue
                   FROM sale_items GROUP BY product_name ORDER BY revenue DESC LIMIT 5""")
    top_products = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("dashboard.html", product_count=product_count,
        customer_count=customer_count, sales_count=sales_count,
        total_sales=total_sales, monthly=monthly, recent_sales=recent_sales,
        stock_items=stock_items, top_products=top_products)

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        try:
            price = float(request.form.get("price", "0"))
            stock = int(request.form.get("stock", "0"))
            if not name or price < 0 or stock < 0:
                raise ValueError
            cur.execute("INSERT INTO products (name, category, price, stock) VALUES (%s,%s,%s,%s)",
                        (name, category, price, stock))
            conn.commit()
            flash("Product added successfully.", "success")
            cur.close(); conn.close()
            return redirect(url_for("products"))
        except ValueError:
            flash("Enter a product name, non-negative price and whole-number stock.", "danger")
    cur.execute("SELECT * FROM products ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("products.html", products=rows)

@app.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id=%s", (product_id,))
    conn.commit()
    cur.close(); conn.close()
    flash("Product deleted if it existed.", "info")
    return redirect(url_for("products"))


@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if name:
            cur.execute(
                "INSERT INTO customers (name, phone, email) VALUES (%s, %s, %s)",
                (name, phone, email)
            )
            conn.commit()
            flash("Customer added successfully.", "success")
            cur.close()
            conn.close()
            return redirect(url_for("customers"))

        flash("Customer name is required.", "danger")

    cur.execute("SELECT * FROM customers ORDER BY id DESC")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("customers.html", customers=rows)


@app.route("/customers/edit/<int:customer_id>", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
    customer = cur.fetchone()

    if customer is None:
        cur.close()
        conn.close()
        flash("Customer not found.", "danger")
        return redirect(url_for("customers"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if not name:
            flash("Customer name is required.", "danger")
            cur.close()
            conn.close()
            return redirect(url_for("edit_customer", customer_id=customer_id))

        cur.execute(
            "UPDATE customers SET name = %s, phone = %s, email = %s WHERE id = %s",
            (name, phone, email, customer_id)
        )
        conn.commit()

        cur.close()
        conn.close()

        flash("Customer details updated successfully.", "success")
        return redirect(url_for("customers"))

    cur.close()
    conn.close()

    return render_template("edit_customer.html", customer=customer)


@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    if request.method == "POST":
        try:
            customer_raw = request.form.get("customer_id", "").strip()
            customer_id = int(customer_raw) if customer_raw else None
            product_id = int(request.form.get("product_id", "0"))
            quantity = int(request.form.get("quantity", "0"))
            if quantity <= 0:
                raise ValueError
            cur.execute("SELECT * FROM products WHERE id=%s FOR UPDATE", (product_id,))
            product = cur.fetchone()
            if not product:
                raise ValueError
            if product["stock"] < quantity:
                flash("Not enough stock for this sale.", "danger")
            else:
                total = round(float(product["price"]) * quantity, 2)
                cur.execute("INSERT INTO sales (customer_id, total_amount) VALUES (%s,%s)",
                            (customer_id, total))
                sale_id = cur.lastrowid
                cur.execute("""INSERT INTO sale_items
                    (sale_id, product_id, product_name, quantity, unit_price, line_total)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (sale_id, product_id, product["name"], quantity, product["price"], total))
                cur.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (quantity, product_id))
                conn.commit()
                flash(f"Sale #{sale_id} saved. Total: ₹{total:.2f}", "success")
                cur.close(); conn.close()
                return redirect(url_for("sales_history"))
        except (ValueError, TypeError):
            flash("Choose a product and enter a valid positive quantity.", "danger")
    cur.execute("SELECT id, name, price, stock FROM products WHERE stock > 0 ORDER BY name")
    product_rows = cur.fetchall()
    cur.execute("SELECT id, name FROM customers ORDER BY name")
    customer_rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("sales.html", products=product_rows, customers=customer_rows)

@app.route("/sales-history")
@login_required
def sales_history():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT s.id, s.sale_date, s.total_amount, COALESCE(c.name, 'Walk-in Customer') AS customer
                   FROM sales s LEFT JOIN customers c ON s.customer_id=c.id
                   ORDER BY s.sale_date DESC""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("sales_history.html", sales=rows)

@app.route("/reports")
@login_required
def reports():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT DATE_FORMAT(sale_date, '%Y-%m') AS month, SUM(total_amount) AS total
                   FROM sales GROUP BY DATE_FORMAT(sale_date, '%Y-%m') ORDER BY month""")
    monthly_rows = cur.fetchall()
    cur.execute("""SELECT product_name, SUM(quantity) AS units, SUM(line_total) AS revenue
                   FROM sale_items GROUP BY product_name ORDER BY revenue DESC""")
    product_rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("reports.html", monthly=monthly_rows, products=product_rows)

@app.route("/reports/chart.png")
@login_required
def report_chart():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT DATE_FORMAT(sale_date, '%Y-%m') AS month, SUM(total_amount) AS total
                   FROM sales GROUP BY DATE_FORMAT(sale_date, '%Y-%m') ORDER BY month""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    fig, ax = plt.subplots(figsize=(8, 4))
    if rows:
        df = pd.DataFrame(rows)
        ax.bar(df["month"], df["total"])
        ax.set_title("Monthly Sales")
        ax.set_xlabel("Month")
        ax.set_ylabel("Sales (₹)")
        plt.xticks(rotation=30, ha="right")
    else:
        ax.text(0.5, 0.5, "No sales data yet. Record a sale to create a chart.",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    fig.tight_layout()
    output = BytesIO()
    fig.savefig(output, format="png")
    plt.close(fig)
    output.seek(0)
    return send_file(output, mimetype="image/png")

@app.route("/reports/export")
@login_required
def export_report():
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute("""SELECT s.id AS sale_id, s.sale_date, COALESCE(c.name, 'Walk-in Customer') AS customer,
                          si.product_name, si.quantity, si.unit_price, si.line_total, s.total_amount
                   FROM sales s LEFT JOIN customers c ON s.customer_id=c.id
                   JOIN sale_items si ON s.id=si.sale_id ORDER BY s.sale_date DESC""")
    rows = cur.fetchall()
    cur.close(); conn.close()
    df = pd.DataFrame(rows)
    output = BytesIO()
    df.to_csv(output, index=False)
    output.seek(0)
    return send_file(output, mimetype="text/csv", as_attachment=True, download_name="sales_report.csv")

if __name__ == "__main__":
    try:
        ensure_demo_user()
        print("Demo login: username=admin, password=admin123")
    except mysql.connector.Error as exc:
        print("Database connection failed. Check MySQL is running and schema.sql was imported.")
        print("Details:", exc)
    app.run(debug=True)
