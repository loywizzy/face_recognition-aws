import boto3
import os
import json
import csv
import logging
from pathlib import Path
from flask import Flask, request, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import datetime
from flask import jsonify



# โหลด environment variables
load_dotenv()

# ตั้งค่า logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("web_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'development-key')

# ตั้งค่าโฟลเดอร์สำหรับเก็บข้อมูล
LOCAL_DATA_DIR = Path('local_data')
LOCAL_DATA_DIR.mkdir(exist_ok=True)

UPLOAD_FOLDER = 'uploads'
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

# ตั้งค่า AWS
def get_aws_clients():
    try:
        session = boto3.Session(
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION_NAME', 'ap-southeast-2')
        )
        
        s3 = session.client("s3")
        return s3, True
    except Exception as e:
        logger.error(f"ไม่สามารถเชื่อมต่อกับ AWS: {e}")
        return None, False

s3_client, aws_connected = get_aws_clients()
s3_bucket = os.getenv('S3_BUCKET', 'face-recognition-classroom')

def load_attendance_data():
    """โหลดข้อมูลการเช็คชื่อจากไฟล์ JSON ตามวันที่ปัจจุบัน"""
    today = datetime.date.today().strftime("%Y%m%d")
    attendance_file = LOCAL_DATA_DIR / f'attendance_{today}.json'
    
    if not attendance_file.exists():
        with open(attendance_file, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=4)
        logger.info(f"สร้างไฟล์ข้อมูลการเช็คชื่อใหม่ (JSON) สำหรับวันนี้: {today}")

    try:
        with open(attendance_file, 'r', encoding='utf-8') as f:
            attendance_data = json.load(f)
        return attendance_data
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"ไม่สามารถโหลดข้อมูลการเช็คชื่อ: {e}")
        return {}

@app.route('/checked')
def checked():
    """แสดงผลหน้าเช็คชื่อและส่งข้อมูลไปให้"""
    students_file = LOCAL_DATA_DIR / 'students.json'
    attendance_data = load_attendance_data()

    try:
        if not students_file.exists():
            logger.warning("ไม่พบไฟล์ students.json")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"error": 'ไม่พบข้อมูลนักเรียน', "students": [], "attendance": {}, "aws_connected": aws_connected, "class_name": "10301203"})
            return render_template('checked.html', students=[], attendance={}, aws_connected=aws_connected, class_name="10301203", error='ไม่พบข้อมูลนักเรียน')

        with open(students_file, 'r', encoding='utf-8') as f:
            students = json.load(f)

        # ค้นหา class_name
        class_name = "ไม่พบข้อมูล"
        if students and students[0]:
            class_name = students[0]['class']

        # ตรวจสอบว่าเป็น AJAX request หรือไม่
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"students": students, "attendance": attendance_data, "aws_connected": aws_connected, "class_name": class_name})
            
        return render_template('checked.html', students=students, attendance=attendance_data, aws_connected=aws_connected, class_name=class_name)
    except FileNotFoundError:
        logger.error("ไม่พบไฟล์ students.json")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": 'ไม่พบไฟล์ students.json', "students": [], "attendance": {}, "aws_connected": aws_connected, "class_name": "ไม่พบข้อมูล"})
        return render_template('checked.html', error='ไม่พบไฟล์ students.json')
    except json.JSONDecodeError as e:
        logger.error(f"ไม่สามารถถอดรหัส JSON จากไฟล์ students.json: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": f'ไม่สามารถถอดรหัส JSON จากไฟล์ students.json: {e}', "students": [], "attendance": {}, "aws_connected": aws_connected, "class_name": "ไม่พบข้อมูล"})
        return render_template('checked.html', error=f'ไม่สามารถถอดรหัส JSON จากไฟล์ students.json: {e}')
    except Exception as e:
        logger.error(f"เกิดข้อผิดพลาดขณะดึงข้อมูล: {e}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"error": f'เกิดข้อผิดพลาดขณะดึงข้อมูล: {e}', "students": [], "attendance": {}, "aws_connected": aws_connected, "class_name": "ไม่พบข้อมูล"})
        return render_template('checked.html', error=f'เกิดข้อผิดพลาดขณะดึงข้อมูล: {e}')

@app.route('/api/attendance')
def api_attendance():
    """API สำหรับส่งข้อมูลการเช็คชื่อในรูปแบบ JSON"""
    students_file = LOCAL_DATA_DIR / 'students.json'
    attendance_data = load_attendance_data()

    try:
        if not students_file.exists():
            logger.warning("ไม่พบไฟล์ students.json")
            return jsonify({
                "success": False,
                "error": 'ไม่พบข้อมูลนักเรียน', 
                "students": [], 
                "attendance": {}, 
                "aws_connected": aws_connected, 
                "class_name": "10301203"
            })

        with open(students_file, 'r', encoding='utf-8') as f:
            students = json.load(f)

        # ค้นหา class_name
        class_name = "ไม่พบข้อมูล"
        if students and students[0]:
            class_name = students[0]['class']

        return jsonify({
            "success": True,
            "students": students, 
            "attendance": attendance_data, 
            "aws_connected": aws_connected, 
            "class_name": class_name,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
            
    except Exception as e:
        logger.error(f"API Error: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "students": [],
            "attendance": {},
            "aws_connected": aws_connected,
            "class_name": "ไม่พบข้อมูล"
        })

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_students_from_file():
    """โหลดข้อมูลนักศึกษาจากไฟล์ CSV"""
    students_file = Path('students.csv')
    
    if not students_file.exists():
        # สร้างไฟล์ตัวอย่าง
        with open(students_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'name', 'class'])
            writer.writerow(['student_378', 'นายสมศรี มีใจ', '10301203'])
            writer.writerow(['student_002', 'นางสาวอัศนีย์ ผิวดี', '10301203'])
            writer.writerow(['student_402', 'นายใจดี มีไหม', '10301203'])
        logger.info("สร้างไฟล์ข้อมูลนักศึกษาตัวอย่าง (CSV) สำเร็จ")
    
    # อ่านข้อมูลจากไฟล์
    students = []
    try:
        with open(students_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                students.append(row)
        logger.info(f"โหลดข้อมูลนักศึกษาจาก CSV สำเร็จ: {len(students)} คน")
    except Exception as e:
        logger.error(f"ไม่สามารถโหลดข้อมูลนักศึกษาจาก CSV: {e}")
        students = []
    
    return students

def update_student_json():
    """อัพเดทไฟล์ students.json จากข้อมูล CSV"""
    students = load_students_from_file()
    json_path = LOCAL_DATA_DIR / 'students.json'
    
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(students, f, ensure_ascii=False, indent=4)
        logger.info(f"อัพเดทไฟล์ students.json สำเร็จ: {len(students)} รายการ")
        return True
    except Exception as e:
        logger.error(f"ไม่สามารถอัพเดทไฟล์ students.json: {e}")
        return False

def save_student_to_csv(student_data):
    """บันทึกข้อมูลนักศึกษาลงในไฟล์ CSV"""
    students_file = Path('students.csv')
    exists = students_file.exists()
    
    try:
        mode = 'a' if exists else 'w'
        with open(students_file, mode, newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            if not exists:
                writer.writerow(['id', 'name', 'class'])
            
            writer.writerow([
                student_data['id'],
                student_data['name'],
                student_data['class']
            ])
        
        logger.info(f"บันทึกข้อมูลนักศึกษา {student_data['id']} ลงในไฟล์ CSV สำเร็จ")
        return True
    except Exception as e:
        logger.error(f"ไม่สามารถบันทึกข้อมูลนักศึกษาลงในไฟล์ CSV: {e}")
        return False

def get_s3_files():
    """ดึงรายการไฟล์จาก S3"""
    if not aws_connected:
        logger.warning("ไม่สามารถดึงรายการไฟล์จาก S3 เนื่องจากไม่ได้เชื่อมต่อกับ AWS")
        return {}
    
    try:
        response = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix="students/")
        
        if 'Contents' not in response:
            return {}
        
        # สร้าง dict โดยใช้ student_id เป็น key
        files = {}
        for obj in response['Contents']:
            file_key = obj['Key']
            if file_key.startswith('students/student_'):
                student_id = file_key.split('/')[1].split('.')[0]  # ดึง student_id จากชื่อไฟล์
                files[student_id] = file_key
        
        return files
    except Exception as e:
        logger.error(f"ไม่สามารถดึงรายการไฟล์จาก S3: {e}")
        return {}

def delete_student(student_id):
    """ลบข้อมูลนักศึกษาจากไฟล์ CSV และรูปภาพจาก S3"""
    # ลบจาก CSV
    students_file = Path('students.csv')
    if not students_file.exists():
        return False
    
    try:
        # อ่านข้อมูลทั้งหมด
        students = []
        with open(students_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['id'] != student_id:
                    students.append(row)
        
        # เขียนข้อมูลกลับไปยังไฟล์
        with open(students_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['id', 'name', 'class'])
            for student in students:
                writer.writerow([student['id'], student['name'], student['class']])
        
        logger.info(f"ลบข้อมูลนักศึกษา {student_id} จากไฟล์ CSV สำเร็จ")
        
        # ลบไฟล์จาก S3
        if aws_connected:
            # ตรวจสอบว่ามีไฟล์นี้ใน S3 หรือไม่
            s3_files = get_s3_files()
            if student_id in s3_files:
                s3_client.delete_object(Bucket=s3_bucket, Key=s3_files[student_id])
                logger.info(f"ลบไฟล์ {s3_files[student_id]} จาก S3 สำเร็จ")
        
        # อัพเดทไฟล์ JSON
        update_student_json()
        
        return True
    except Exception as e:
        logger.error(f"ไม่สามารถลบข้อมูลนักศึกษา: {e}")
        return False

def generate_presigned_url(file_key, expiration=3600):
    """สร้าง presigned URL สำหรับเข้าถึงไฟล์ใน S3"""
    if not aws_connected:
        return None
    
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': s3_bucket, 'Key': file_key},
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        logger.error(f"ไม่สามารถสร้าง presigned URL: {e}")
        return None

@app.route('/')
def index():
    students = load_students_from_file()
    s3_files = get_s3_files()
    
    # สร้าง presigned URL สำหรับไฟล์แต่ละไฟล์
    image_urls = {}
    for student_id, file_key in s3_files.items():
        url = generate_presigned_url(file_key)
        if url:
            image_urls[student_id] = url
    
    return render_template('index.html', students=students, aws_connected=aws_connected, image_urls=image_urls)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('ไม่พบไฟล์ในการอัพโหลด', 'error')
        return redirect(request.url)
    
    file = request.files['file']
    student_id = request.form.get('student_id', '')
    
    if file.filename == '':
        flash('ไม่ได้เลือกไฟล์', 'error')
        return redirect(request.url)
    
    if not student_id:
        flash('กรุณาระบุรหัสนักศึกษา', 'error')
        return redirect(request.url)
    
    if not student_id.startswith('student_'):
        student_id = f"student_{student_id}"
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_ext = filename.rsplit('.', 1)[1].lower()
        s3_filename = f"{student_id}.{file_ext}"
        local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        try:
            # บันทึกไฟล์ไว้ในเครื่อง
            file.save(local_path)
            
            # อัพโหลดไฟล์ไปยัง S3
            if aws_connected:
                s3_client.upload_file(
                    local_path, 
                    s3_bucket, 
                    f"students/{s3_filename}"
                )
                logger.info(f"อัพโหลดไฟล์ {s3_filename} ไปยัง S3 สำเร็จ")
                flash(f"อัพโหลดไฟล์ {s3_filename} ไปยัง S3 สำเร็จ", 'success')
            else:
                logger.warning("ไม่สามารถอัพโหลดไปยัง S3 เนื่องจากไม่ได้เชื่อมต่อกับ AWS")
                flash("บันทึกไฟล์ไว้ในเครื่องเท่านั้น เนื่องจากไม่ได้เชื่อมต่อกับ AWS", 'warning')
            
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการอัพโหลดไฟล์: {e}")
            flash(f"เกิดข้อผิดพลาด: {str(e)}", 'error')
    else:
        flash('ไฟล์ที่อนุญาตคือ png, jpg, jpeg เท่านั้น', 'error')
    
    return redirect(url_for('index'))

@app.route('/add_student', methods=['POST'])
def add_student():
    student_id = request.form.get('student_id', '')
    student_name = request.form.get('student_name', '')
    student_class = request.form.get('student_class', '')
    
    if not student_id or not student_name or not student_class:
        flash('กรุณากรอกข้อมูลให้ครบถ้วน', 'error')
        return redirect(url_for('index'))
    
    if not student_id.startswith('student_'):
        student_id = f"student_{student_id}"
    
    student_data = {
        'id': student_id,
        'name': student_name,
        'class': student_class
    }
    
    if save_student_to_csv(student_data):
        update_student_json()
        flash('เพิ่มข้อมูลนักศึกษาสำเร็จ', 'success')
    else:
        flash('ไม่สามารถบันทึกข้อมูลนักศึกษา', 'error')
    
    return redirect(url_for('index'))

@app.route('/update_json', methods=['POST'])
def update_json():
    if update_student_json():
        flash('อัพเดทไฟล์ students.json สำเร็จ', 'success')
    else:
        flash('ไม่สามารถอัพเดทไฟล์ students.json', 'error')
    
    return redirect(url_for('index'))

@app.route('/delete_student/<student_id>', methods=['POST'])
def delete_student_route(student_id):
    if delete_student(student_id):
        flash(f'ลบข้อมูลนักศึกษา {student_id} สำเร็จ', 'success')
    else:
        flash(f'ไม่สามารถลบข้อมูลนักศึกษา {student_id}', 'error')
    
    return redirect(url_for('index'))

if __name__ == '__main__':
    # สร้าง templates directory ถ้ายังไม่มี
    templates_dir = Path('templates')
    templates_dir.mkdir(exist_ok=True)
    
    # สร้างไฟล์ template ถ้ายังไม่มี
    template_file = templates_dir / 'index.html'
    if not template_file.exists():
        with open(template_file, 'w', encoding='utf-8') as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>ระบบจัดการข้อมูลนักศึกษา</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" type="image/png" sizes="16x16" href="https://cdn-icons-png.freepik.com/512/13320/13320393.png"/>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .container { max-width: 960px; margin-top: 30px; }
        .flash-messages { margin-bottom: 20px; }
        .aws-status { margin-bottom: 20px; }
        .aws-connected { color: green; }
        .aws-disconnected { color: red; }
        .student-image { width: 50px; height: 50px; object-fit: cover; border-radius: 50%; }
        .no-image { width: 50px; height: 50px; background-color: #f0f0f0; border-radius: 50%; 
                    display: flex; align-items: center; justify-content: center; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">ระบบจัดการข้อมูลนักศึกษา</h1>
        
        <div class="aws-status">
            สถานะการเชื่อมต่อ AWS: 
            {% if aws_connected %}
                <span class="aws-connected">เชื่อมต่อแล้ว</span>
            {% else %}
                <span class="aws-disconnected">ไม่ได้เชื่อมต่อ</span>
            {% endif %}
        </div>
        
        <div class="flash-messages">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category if category != 'error' else 'danger' }}">
                            {{ message }}
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>
        
        <div class="row">
            <div class="col-md-6">
                <div class="card mb-4">
                    <div class="card-header">
                        <h2 class="h5 mb-0">อัพโหลดรูปภาพนักศึกษา</h2>
                    </div>
                    <div class="card-body">
                        <form action="/upload" method="POST" enctype="multipart/form-data">
                            <div class="mb-3">
                                <label for="student_id" class="form-label">รหัสนักศึกษา</label>
                                <input type="text" class="form-control" id="student_id" name="student_id" placeholder="เช่น 378 (ไม่ต้องใส่ student_)">
                                <div class="form-text">ระบบจะเพิ่ม "student_" ให้อัตโนมัติ</div>
                            </div>
                            <div class="mb-3">
                                <label for="file" class="form-label">เลือกรูปภาพ</label>
                                <input class="form-control" type="file" id="file" name="file" accept="image/png, image/jpeg, image/jpg">
                            </div>
                            <button type="submit" class="btn btn-primary">อัพโหลด</button>
                        </form>
                    </div>
                </div>
            </div>
            
            <div class="col-md-6">
                <div class="card mb-4">
                    <div class="card-header">
                        <h2 class="h5 mb-0">เพิ่มข้อมูลนักศึกษา</h2>
                    </div>
                    <div class="card-body">
                        <form action="/add_student" method="POST">
                            <div class="mb-3">
                                <label for="add_student_id" class="form-label">รหัสนักศึกษา</label>
                                <input type="text" class="form-control" id="add_student_id" name="student_id" placeholder="เช่น 378 (ไม่ต้องใส่ student_)">
                                <div class="form-text">ระบบจะเพิ่ม "student_" ให้อัตโนมัติ</div>
                            </div>
                            <div class="mb-3">
                                <label for="student_name" class="form-label">ชื่อ-นามสกุล</label>
                                <input type="text" class="form-control" id="student_name" name="student_name" placeholder="เช่น นายสมศรี มีใจ">
                            </div>
                            <div class="mb-3">
                                <label for="student_class" class="form-label">รหัสชั้นเรียน</label>
                                <input type="text" class="form-control" id="student_class" name="student_class" placeholder="เช่น 10301203">
                            </div>
                            <button type="submit" class="btn btn-success">เพิ่มข้อมูล</button>
                        </form>
                    </div>
                </div>
                
                <div class="card mb-4">
                    <div class="card-header">
                        <h2 class="h5 mb-0">อัพเดทไฟล์ JSON</h2>
                    </div>
                    <div class="card-body">
                        <form action="/update_json" method="POST">
                            <button type="submit" class="btn btn-warning">อัพเดทไฟล์ students.json</button>
                            <div class="form-text mt-2">ใช้เมื่อมีการแก้ไขไฟล์ students.csv โดยตรง</div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h2 class="h5 mb-0">รายชื่อนักศึกษา</h2>
            </div>
            <div class="card-body">
                {% if students %}
                    <div class="table-responsive">
                        <table class="table table-striped">
                            <thead>
                                <tr>
                                    <th>รูปภาพ</th>
                                    <th>รหัสนักศึกษา</th>
                                    <th>ชื่อ-นามสกุล</th>
                                    <th>รหัสชั้นเรียน</th>
                                    <th>การจัดการ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for student in students %}
                                    <tr>
                                        <td>
                                            {% if student.id in image_urls %}
                                                <img src="{{ image_urls[student.id] }}" alt="{{ student.name }}" class="student-image">
                                            {% else %}
                                                <div class="no-image">
                                                    <i class="bi bi-person"></i>
                                                </div>
                                            {% endif %}
                                        </td>
                                        <td>{{ student.id }}</td>
                                        <td>{{ student.name }}</td>
                                        <td>{{ student.class }}</td>
                                        <td>
                                            <form action="{{ url_for('delete_student_route', student_id=student.id) }}" method="POST" onsubmit="return confirm('คุณต้องการลบข้อมูลนักศึกษานี้ใช่หรือไม่?');">
                                                <button type="submit" class="btn btn-danger btn-sm">ลบ</button>
                                            </form>
                                        </td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <div class="alert alert-info">ยังไม่มีข้อมูลนักศึกษา</div>
                {% endif %}
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.0/font/bootstrap-icons.css"></script>
</body>
</html>""")
    
    # อัพเดทไฟล์ students.json เมื่อเริ่มต้นแอพ
    update_student_json()
    
    app.run(debug=True)