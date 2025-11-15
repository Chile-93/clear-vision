from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text
from db import get_engine

app = Flask(__name__)
engine = get_engine()


# ---------- ROUTES ----------

# Dashboard
@app.route('/')
def dashboard():
    query = """
    SELECT
        (SELECT COUNT(*) FROM patients) AS total_patients,
        (SELECT COUNT(*) FROM doctor) AS total_doctors,
        (SELECT COUNT(*) FROM visit) AS total_visits,
        (SELECT COUNT(*) FROM prescriptions) AS total_prescriptions
    """
    recent_patients_query = """
    SELECT p.patient_id, p.first_name, p.last_name, p.gender, p.phone, v.visit_date
    FROM patients p
    LEFT JOIN visit v ON p.patient_id = v.patient_id
    ORDER BY v.visit_date DESC
    LIMIT 20
    """
    with engine.connect() as conn:
        result = conn.execute(text(query)).fetchone()
        results = conn.execute(text(recent_patients_query)).fetchall()
    return render_template(
        'dashboard.html',
        total_patients=result.total_patients,
        total_doctors=result.total_doctors,
        total_visits=result.total_visits,
        total_prescriptions=result.total_prescriptions,
        results=results
    )


# Search
@app.route('/search', methods=['POST'])
def search():
    q = (request.form.get('query') or request.form.get('name') or "").strip()
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    sql = "SELECT * FROM vw_patientfullreport WHERE 1=1"
    params = {}

    if q:
        sql += """
            AND (
                LOWER(first_name) LIKE LOWER(:name)
                OR LOWER(last_name) LIKE LOWER(:name)
                OR LOWER(first_name || ' ' || last_name) LIKE LOWER(:name)
                OR CAST(patient_id AS TEXT) = :exact_id
            )
        """
        params["name"] = f"%{q}%"
        params["exact_id"] = q

    if start_date:
        sql += " AND visit_date >= :start_date"
        params["start_date"] = start_date
    if end_date:
        sql += " AND visit_date <= :end_date"
        params["end_date"] = end_date

    sql += " ORDER BY visit_date DESC LIMIT 500"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        results = [dict(r._mapping) for r in rows]

    if not results and q:
        fallback_sql = """
            SELECT p.patient_id, p.first_name, p.last_name, p.gender, p.phone, p.email,
                   NULL AS visit_date, NULL AS doctor_name, NULL AS diagnosis, NULL AS drug_name
            FROM patients p
            WHERE LOWER(p.first_name) LIKE LOWER(:name)
               OR LOWER(p.last_name) LIKE LOWER(:name)
               OR LOWER(first_name || ' ' || last_name) LIKE LOWER(:name)
               OR CAST(patient_id AS TEXT) = :exact_id
            ORDER BY patient_id DESC LIMIT 200
        """
        fallback_params = {"name": f"%{q}%", "exact_id": q}
        with engine.connect() as conn:
            fb_rows = conn.execute(text(fallback_sql), fallback_params).fetchall()
            results = [dict(r._mapping) for r in fb_rows]

    return render_template('search_results.html', results=results)


# Patient details
@app.route('/patient/<int:patient_id>')
def patient_detail(patient_id):
    sql = """
        SELECT *
        FROM vw_patientfullreport
        WHERE patient_id = :pid
        ORDER BY visit_date DESC
    """

    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"pid": patient_id}).fetchall()

    if not rows:
        return render_template("error.html", message="Patient not found")

    # first row always contains patient info
    patient_info = rows[0]

    return render_template(
        "patient_detail.html",
        patient=patient_info,
        visits=rows  # 👈 FIXED
    )




@app.route('/delete_visit/<int:visit_id>/<int:patient_id>', methods=['POST'])
def delete_visit(visit_id, patient_id):
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM visit WHERE visit_id = :vid"), {"vid": visit_id})

        return redirect(url_for('patient_detail', patient_id=patient_id))

    except Exception as e:
        return f"❌ Error deleting visit: {str(e)}", 500




# Add patient
@app.route('/add_patient', methods=['GET', 'POST'])
def add_patient():
    if request.method == 'POST':
        data = {k: request.form.get(k) for k in [
            "first_name", "last_name", "dob", "gender", "phone", "email",
            "home_address", "blood_group", "genotype", "next_of_kin",
            "next_of_kin_phone", "registration_date"
        ]}
        query = """
        INSERT INTO patients (
            first_name, last_name, dob, gender, phone, email, home_address,
            blood_group, genotype, next_of_kin, next_of_kin_phone, registration_date
        ) VALUES (
            :first_name, :last_name, :dob, :gender, :phone, :email, :home_address,
            :blood_group, :genotype, :next_of_kin, :next_of_kin_phone, :registration_date
        )
        RETURNING patient_id
        """
        try:
            with engine.begin() as conn:
                patient_id = conn.execute(text(query), data).scalar()
            return render_template("success.html", message="Patient added successfully!", patient_id=patient_id)
        except Exception as e:
            return render_template("error.html", message=str(e))
    return render_template('add_patient.html')


# Add visit
@app.route("/add_visit", methods=["GET", "POST"])
def add_visit():
    if request.method == "POST":
        try:
            patient_id = request.form["patient_id"]
            visit_data = {k: request.form.get(k) for k in [
                "visit_date", "doctor_id", "diagnosis", "treatment",
                "visual_acuity_left", "visual_acuity_right",
                "intraocular_pressure", "follow_up_date"
            ]}
            presc_data = {k: request.form.get(k) for k in ["drug_name", "dosage", "duration", "notes"]}
            mh_data = {k: request.form.get(k) for k in ["condition", "diagnosis_date", "under_medication", "mh_notes"]}

            with engine.begin() as conn:
                if any(mh_data.values()):
                    conn.execute(text("""
                        INSERT INTO medical_history (patient_id, condition, diagnosis_date, under_medication, notes)
                        VALUES (:patient_id, :condition, :diagnosis_date, :under_medication, :notes)
                    """), {**mh_data, "patient_id": patient_id})

                visit_result = conn.execute(text("""
                    INSERT INTO visit (
                        patient_id, doctor_id, visit_date, diagnosis, treatment,
                        visual_acuity_left, visual_acuity_right, intraocular_pressure, follow_up_date
                    )
                    VALUES (
                        :patient_id, :doctor_id, :visit_date, :diagnosis, :treatment,
                        :visual_acuity_left, :visual_acuity_right, :intraocular_pressure, :follow_up_date
                    )
                    RETURNING visit_id
                """), {**visit_data, "patient_id": patient_id})
                new_visit_id = visit_result.scalar()

                if presc_data["drug_name"]:
                    conn.execute(text("""
                        INSERT INTO prescriptions (patient_id, doctor_id, visit_id, drug_name, dosage, duration, notes)
                        VALUES (:patient_id, :doctor_id, :visit_id, :drug_name, :dosage, :duration, :notes)
                    """), {**presc_data, "patient_id": patient_id, "doctor_id": visit_data.get("doctor_id"), "visit_id": new_visit_id})

            return render_template("success.html", message="✅ Visit, prescription, and medical history saved successfully!", patient_id=patient_id)

        except Exception as e:
            return render_template("error.html", message=f"❌ Error: {e}")

    with engine.connect() as conn:
        doctors = conn.execute(text("SELECT doctor_id, doctor_name FROM doctor ORDER BY doctor_name")).fetchall()
    return render_template("add_visit.html", doctors=doctors)


# Delete patient (PostgreSQL-compatible using USING)
@app.route('/delete_patient/<int:patient_id>', methods=['POST'])
def delete_patient(patient_id):
    try:
        with engine.begin() as conn:

            # 1️⃣ Delete prescriptions linked to visits for this patient
            conn.execute(text("""
                DELETE FROM prescriptions 
                WHERE visit_id IN (
                    SELECT visit_id FROM visit WHERE patient_id = :id
                )
            """), {"id": patient_id})

            # 2️⃣ Delete visits
            conn.execute(text("""
                DELETE FROM visit WHERE patient_id = :id
            """), {"id": patient_id})

            # 3️⃣ Delete medical history
            conn.execute(text("""
                DELETE FROM medical_history WHERE patient_id = :id
            """), {"id": patient_id})

            # 4️⃣ Delete folder
            conn.execute(text("""
                DELETE FROM folder WHERE patient_id = :id
            """), {"id": patient_id})

            # 5️⃣ Finally delete patient
            result = conn.execute(text("""
                DELETE FROM patients WHERE patient_id = :id
            """), {"id": patient_id})

        if result.rowcount == 0:
            return render_template("error.html", message="❌ Patient not found")

        return render_template("delete_success.html", message="✅ Patient and all related records deleted successfully!")

    except Exception as e:
        return render_template("error.html", message=f"❌ Error deleting patient: {e}")




@app.route('/view_patients')
def view_patients():
    with engine.connect() as conn:
        patients = conn.execute(text("SELECT * FROM patients ORDER BY registration_date DESC")).fetchall()
    return render_template('view_patients.html', patients=patients)


@app.route("/reports")
def reports():
    query = """
    SELECT
        (SELECT COUNT(*) FROM patients) AS total_patients,
        (SELECT COUNT(*) FROM doctor) AS total_doctors,
        (SELECT COUNT(*) FROM visit) AS total_visits,
        (SELECT COUNT(*) FROM prescriptions) AS total_prescriptions,
        (SELECT COUNT(*) FROM medical_history) AS total_histories,
        (SELECT COUNT(*) FROM folder) AS total_folders
    """
    with engine.connect() as conn:
        summary = conn.execute(text(query)).fetchone()
    return render_template("reports.html", summary=summary)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    clinic_name = "Auspron Eye Clinic"
    address = "123 Main Street, City"
    phone = "0800-123-4567"
    email = "info@auspronclinic.com"
    if request.method == "POST":
        clinic_name = request.form.get("clinic_name")
        address = request.form.get("address")
        phone = request.form.get("phone")
        email = request.form.get("email")
        flash("✅ Settings updated successfully!", "success")
    return render_template("settings.html", clinic_name=clinic_name, address=address, phone=phone, email=email)


if __name__ == '__main__':
    app.run(debug=True)
