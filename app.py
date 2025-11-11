from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text
from db import engine  # import the shared database connection
from db import get_engine
from sqlalchemy.exc import IntegrityError

app = Flask(__name__)
engine = get_engine()


# ---------- ROUTES ----------

# Dashboard (home)
@app.route('/')
def dashboard():
    """Render the main dashboard with totals"""
    query = """
    SELECT
        (SELECT COUNT(*) FROM patients) AS total_patients,
        (SELECT COUNT(*) FROM doctor) AS total_doctors,
        (SELECT COUNT(*) FROM visit) AS total_visits,
        (SELECT COUNT(*) FROM prescriptions) AS total_prescriptions
    """

    with engine.connect() as conn:
        result = conn.execute(text(query)).fetchone()

    # To populate the recent patients table
    recent_patients_query = """
    SELECT TOP 20 p.patient_id, p.first_name, p.last_name, p.gender, p.phone, v.visit_date
    FROM patients p
    LEFT JOIN visit v ON p.patient_id = v.patient_id
    ORDER BY v.visit_date DESC
    """

    with engine.connect() as conn:
        results = conn.execute(text(recent_patients_query)).fetchall()

    return render_template(
        'dashboard.html',
        total_patients=result.total_patients,
        total_doctors=result.total_doctors,
        total_visits=result.total_visits,
        total_prescriptions=result.total_prescriptions,
        results=results
    )





# Search route

@app.route('/search', methods=['POST'])
def search():
    # Get the user input (support both 'query' and 'name' just in case)
    q = (request.form.get('query') or request.form.get('name') or "").strip()
    start_date = request.form.get('start_date') or None
    end_date = request.form.get('end_date') or None

    # Prepare base SQL that queries the view (vw_PatientFullReport)
    sql = """
        SELECT TOP 500 *
        FROM vw_PatientFullReport
        WHERE 1=1
    """
    params = {}

    # If user provided a q, search first_name, last_name, and full name
    if q:
        # Use LOWER(...) to make it case-insensitive, and search full name by concatenation
        # In SQL Server the string concatenation operator is +.
        sql += """
            AND (
                LOWER(first_name) LIKE LOWER(:name)
                OR LOWER(last_name) LIKE LOWER(:name)
                OR LOWER(first_name + ' ' + last_name) LIKE LOWER(:name)
                OR CAST(patient_id AS NVARCHAR(100)) = :exact_id
            )
        """
        params["name"] = f"%{q}%"
        params["exact_id"] = q

    # Date filters (only if provided)
    if start_date:
        sql += " AND visit_date >= :start_date"
        params["start_date"] = start_date
    if end_date:
        sql += " AND visit_date <= :end_date"
        params["end_date"] = end_date

    sql += " ORDER BY visit_date DESC"

    # DEBUG: log SQL & params so you can check Flask console
    app.logger.debug("Search SQL: %s", sql)
    app.logger.debug("Search params: %s", params)

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        results = [dict(r._mapping) for r in rows]

    # If the view returned nothing but the user searched by name, do a fallback search in patients table
    # (useful if a patient exists but has no related visit/prescription rows in the view)
    if not results and q:
        fallback_sql = """
            SELECT TOP 200
                p.patient_id, p.first_name, p.last_name, p.gender, p.phone, p.email,
                NULL AS visit_date, NULL AS doctor_name, NULL AS diagnosis, NULL AS drug_name
            FROM patients p
            WHERE LOWER(p.first_name) LIKE LOWER(:name)
               OR LOWER(p.last_name) LIKE LOWER(:name)
               OR LOWER(p.first_name + ' ' + p.last_name) LIKE LOWER(:name)
               OR CAST(p.patient_id AS NVARCHAR(100)) = :exact_id
            ORDER BY p.patient_id DESC
        """
        fallback_params = {"name": f"%{q}%", "exact_id": q}
        app.logger.debug("Fallback SQL: %s", fallback_sql)
        app.logger.debug("Fallback params: %s", fallback_params)
        with engine.connect() as conn:
            fb_rows = conn.execute(text(fallback_sql), fallback_params).fetchall()
            results = [dict(r._mapping) for r in fb_rows]

    return render_template('search_results.html', results=results)











@app.route('/patient/<int:patient_id>')
def patient_detail(patient_id):
    sql = """
        SELECT *
        FROM vw_PatientFullReport
        WHERE patient_id = :pid
        ORDER BY visit_date DESC
    """

    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"pid": patient_id}).fetchall()

    if not rows:
        return render_template("error.html", message="Patient not found")

    patient = dict(rows[0]._mapping)

    # Remove duplicates by visit_id
    seen = set()
    unique_visits = []
    for r in rows:
        v = dict(r._mapping)
        if v['visit_id'] not in seen:
            seen.add(v['visit_id'])
            unique_visits.append(v)

    visits = unique_visits

    return render_template('patient_detail.html', patient=patient, visits=visits)






# ---------- DELETE PATIENT WORKFLOW ----------

# GET route to show confirmation page
@app.route('/delete_patient/<int:patient_id>', methods=['GET'])
def confirm_delete_patient(patient_id):
    with engine.connect() as conn:
        patient = conn.execute(
            text("SELECT * FROM patients WHERE patient_id = :id"), {"id": patient_id}
        ).fetchone()

    if not patient:
        return render_template("error.html", message="❌ Patient not found")

    return render_template("confirm_delete.html", patient=patient, patient_id=patient_id)


# POST route to actually delete patient and related records
@app.route('/delete_patient/<int:patient_id>', methods=['POST'])
def delete_patient(patient_id):
    try:
        with engine.begin() as conn:
            # 1️⃣ Delete prescriptions linked to patient's visits
            conn.execute(text("""
                DELETE pr
                FROM prescriptions pr
                INNER JOIN visit v ON pr.visit_id = v.visit_id
                WHERE v.patient_id = :id
            """), {"id": patient_id})

            # 2️⃣ Delete visits
            conn.execute(text("DELETE FROM visit WHERE patient_id = :id"), {"id": patient_id})

            # 3️⃣ Delete medical history
            conn.execute(text("DELETE FROM medical_history WHERE patient_id = :id"), {"id": patient_id})

            # 4️⃣ Delete folder
            conn.execute(text("DELETE FROM folder WHERE patient_id = :id"), {"id": patient_id})

            # 5️⃣ Delete patient
            result = conn.execute(text("DELETE FROM patients WHERE patient_id = :id"), {"id": patient_id})

        if result.rowcount == 0:
            return render_template("error.html", message="❌ Patient not found")

        return render_template("delete_success.html", message="✅ Patient and all related records deleted successfully!")

    except Exception as e:
        return render_template("error.html", message=f"❌ Error deleting patient: {e}")



@app.route("/delete_visit/<int:visit_id>/<int:patient_id>", methods=["POST"])
def delete_visit(visit_id, patient_id):
    try:
        with engine.begin() as conn:
            # Delete prescriptions linked to that visit
            conn.execute(text("DELETE FROM prescriptions WHERE visit_id = :visit_id"), {"visit_id": visit_id})
            # Delete the visit
            conn.execute(text("DELETE FROM visit WHERE visit_id = :visit_id"), {"visit_id": visit_id})

        return render_template(
            "success_single_delete.html",
            message="✅ Visit deleted successfully!",
            patient_id=patient_id
        )

    except Exception as e:
        return render_template("error.html", message=f"❌ Error deleting visit: {e}")




# Add new patient
@app.route('/add_patient', methods=['GET', 'POST'])
def add_patient():
    if request.method == 'POST':
        data = {
            "first_name": request.form.get('first_name'),
            "last_name": request.form.get('last_name'),
            "dob": request.form.get('dob'),
            "gender": request.form.get('gender'),
            "phone": request.form.get('phone'),
            "email": request.form.get('email'),
            "home_address": request.form.get('home_address'),
            "blood_group": request.form.get('blood_group'),
            "genotype": request.form.get('genotype'),
            "next_of_kin": request.form.get('next_of_kin'),
            "next_of_kin_phone": request.form.get('next_of_kin_phone'),
            "registration_date": request.form.get('registration_date')
        }

        query = """
        INSERT INTO patients (
            first_name, last_name, dob, gender, phone, email, home_address,
            blood_group, genotype, next_of_kin, next_of_kin_phone, registration_date
        ) VALUES (
            :first_name, :last_name, :dob, :gender, :phone, :email, :home_address,
            :blood_group, :genotype, :next_of_kin, :next_of_kin_phone, :registration_date
        )
        """

        try:
            with engine.begin() as conn:
                conn.execute(text(query), data)
            return render_template("success.html", message="Patient added successfully!")     
        except Exception as e:
            return render_template("error.html", message=str(e))

    return render_template('add_patient.html')




@app.route('/view_patients')
def view_patients():
    """View all patients"""
    query = "SELECT * FROM patients ORDER BY registration_date DESC"
    with engine.connect() as conn:
        patients = conn.execute(text(query)).fetchall()
    return render_template('view_patients.html', patients=patients)



@app.route("/add_visit", methods=["GET", "POST"])
def add_visit():
    if request.method == "POST":
        try:
            patient_id = request.form["patient_id"]
            visit_date = request.form["visit_date"]
            doctor_id = request.form.get("doctor_id") or None
            diagnosis = request.form.get("diagnosis")
            treatment = request.form.get("treatment")
            visual_acuity_left = request.form.get("visual_acuity_left")
            visual_acuity_right = request.form.get("visual_acuity_right")
            intraocular_pressure = request.form.get("intraocular_pressure")
            follow_up_date = request.form.get("follow_up_date")

            # Prescription info
            drug_name = request.form.get("drug_name")
            dosage = request.form.get("dosage")
            duration = request.form.get("duration")
            notes = request.form.get("notes")

            # Medical history info (fields that exist in your schema)
            mh_condition = request.form.get("condition")
            mh_diagnosis_date = request.form.get("diagnosis_date")
            mh_under_medication = request.form.get("under_medication")
            mh_notes = request.form.get("mh_notes")

            with engine.begin() as conn:
                # 1) Insert medical_history if any of the medical history fields were provided
                if mh_condition or mh_diagnosis_date or mh_under_medication or mh_notes:
                    conn.execute(text("""
                        INSERT INTO medical_history (patient_id, condition, diagnosis_date, under_medication, notes)
                        VALUES (:patient_id, :condition, :diagnosis_date, :under_medication, :notes)
                    """), {
                        "patient_id": patient_id,
                        "condition": mh_condition,
                        "diagnosis_date": mh_diagnosis_date,
                        "under_medication": mh_under_medication,
                        "notes": mh_notes
                    })

                # 2) Insert visit and get inserted visit_id
                visit_result = conn.execute(text("""
                    INSERT INTO visit (
                        patient_id, doctor_id, visit_date, diagnosis, treatment,
                        visual_acuity_left, visual_acuity_right, intraocular_pressure, follow_up_date
                    )
                    OUTPUT inserted.visit_id
                    VALUES (
                        :patient_id, :doctor_id, :visit_date, :diagnosis, :treatment,
                        :visual_acuity_left, :visual_acuity_right, :intraocular_pressure, :follow_up_date
                    )
                """), {
                    "patient_id": patient_id,
                    "doctor_id": doctor_id,
                    "visit_date": visit_date,
                    "diagnosis": diagnosis,
                    "treatment": treatment,
                    "visual_acuity_left": visual_acuity_left,
                    "visual_acuity_right": visual_acuity_right,
                    "intraocular_pressure": intraocular_pressure,
                    "follow_up_date": follow_up_date
                })

                new_visit_id = visit_result.scalar()

                # 3) Insert prescription linked to the new visit if drug_name provided
                if drug_name:
                    conn.execute(text("""
                        INSERT INTO prescriptions (patient_id, doctor_id, visit_id, drug_name, dosage, duration, notes)
                        VALUES (:patient_id, :doctor_id, :visit_id, :drug_name, :dosage, :duration, :notes)
                    """), {
                        "patient_id": patient_id,
                        "doctor_id": doctor_id,
                        "visit_id": new_visit_id,
                        "drug_name": drug_name,
                        "dosage": dosage,
                        "duration": duration,
                        "notes": notes
                    })
                    # After saving the visit
            return render_template(
                     "success.html",
                      message="✅ Visit, prescription, and medical history saved successfully!",
                     patient_id=patient_id 
                          )



            # return render_template("success.html", message="✅ Visit, prescription, and medical history saved successfully!")

        except Exception as e:
            return render_template("error.html", message=f"❌ Error: {e}")

    # GET: load doctors for the dropdown
    with engine.connect() as conn:
        doctors = conn.execute(text("SELECT doctor_id, doctor_name FROM doctor ORDER BY doctor_name")).fetchall()

    return render_template("add_visit.html", doctors=doctors)








@app.route("/reports")
def reports():
    """Generate dashboard-like summary reports"""
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
    """Clinic settings page"""
    # Example: fetching some settings from a table (optional)
    clinic_name = "Auspron Eye Clinic"
    address = "123 Main Street, City"
    phone = "0800-123-4567"
    email = "info@auspronclinic.com"

    if request.method == "POST":
        # Process updated settings
        clinic_name = request.form.get("clinic_name")
        address = request.form.get("address")
        phone = request.form.get("phone")
        email = request.form.get("email")
        
        # Here you would normally save these settings to a DB table
        flash("✅ Settings updated successfully!", "success")

    return render_template(
        "settings.html",
        clinic_name=clinic_name,
        address=address,
        phone=phone,
        email=email
    )
  


# Run app
if __name__ == '__main__':
    app.run(debug=True)
