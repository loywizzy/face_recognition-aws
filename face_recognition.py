import boto3
import cv2
import time
import os
import json
import threading
import datetime
import pygame
import configparser
import logging
from dotenv import load_dotenv
from pathlib import Path
import csv

# ตั้งค่า logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("attendance_system.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# โหลด environment variables จาก .env file
load_dotenv()

# ฟังก์ชัน config
def load_config():
    config = configparser.ConfigParser()
    config_file = Path('config.ini')
    
    if not config_file.exists():
        # สร้างไฟล์ config เริ่มต้นถ้าไม่มี
        config['AWS'] = {
            'region_name': 'ap-southeast-2',
            's3_bucket': 'face-recognition-classroom'
        }
        config['SETTINGS'] = {
            'scan_interval': '1',
            'similarity_threshold': '80',
            'duplicate_check_minutes': '5'
        }
        config['UI'] = {
            'window_name': 'ระบบเช็คชื่อด้วยใบหน้า',
            'font_scale': '0.7',
            'enable_sound': 'True'
        }
        
        with open('config.ini', 'w') as f:
            config.write(f)
    else:
        config.read('config.ini')
    
    return config

# โหลด config
config = load_config()

# ตั้งค่า AWS จาก environment variables
try:
    # กำหนดให้ใช้ environment variables หรือ AWS credentials file
    session = boto3.Session(
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=config['AWS']['region_name']
    )
    
    s3 = session.client("s3")
    rekognition = session.client("rekognition")
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table("Attendance")
    
    AWS_CONNECTED = True
    logger.info("เชื่อมต่อกับ AWS สำเร็จ")
except Exception as e:
    AWS_CONNECTED = False
    logger.error(f"ไม่สามารถเชื่อมต่อกับ AWS: {e}")

# ตั้งค่าเสียง
pygame.mixer.init()
SOUND_ENABLED = config.getboolean('UI', 'enable_sound')

# ตรวจสอบและโหลดไฟล์เสียง
SOUND_SUCCESS = None
if SOUND_ENABLED:
    try:
        sound_file = Path('sounds/success.mp3')
        if sound_file.exists():
            SOUND_SUCCESS = pygame.mixer.Sound('sounds/success.mp3')
        else:
            # สร้างโฟลเดอร์เก็บเสียงถ้าไม่มี
            os.makedirs('sounds', exist_ok=True)
            logger.warning("ไม่พบไฟล์เสียง success.mp3 ในโฟลเดอร์ sounds")
    except Exception as e:
        logger.error(f"ไม่สามารถโหลดไฟล์เสียง: {e}")
        SOUND_ENABLED = False

# ตั้งค่าโฟลเดอร์สำหรับเก็บข้อมูล
LOCAL_DATA_DIR = Path('local_data')
LOCAL_DATA_DIR.mkdir(exist_ok=True)

# คลาส AttendanceSystem
class AttendanceSystem:
    def __init__(self):
        self.cap = None
        self.student_ids = []
        self.attendance_records = {}
        self.last_scan_time = 0
        self.scan_interval = config.getfloat('SETTINGS', 'scan_interval')
        self.similarity_threshold = config.getfloat('SETTINGS', 'similarity_threshold')
        self.duplicate_check_minutes = config.getfloat('SETTINGS', 'duplicate_check_minutes')
        self.window_name = config['UI']['window_name']
        self.font_scale = config.getfloat('UI', 'font_scale')
        self.s3_bucket = config['AWS']['s3_bucket']
        
        # สถานะการทำงาน
        self.processing = False
        self.running = True
        self.checked_in_students = {}
        
        # โหลด face cascade
        try:
            self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            if self.face_cascade.empty():
                logger.error("ไม่สามารถโหลด haarcascade_frontalface_default.xml")
                raise Exception("Face cascade ไม่สามารถโหลดได้")
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการโหลด face cascade: {e}")
            raise
        
        # โหลดข้อมูลนักศึกษา
        self.load_student_data()
        
        # โหลดข้อมูลการเช็คชื่อที่บันทึกไว้ในระบบ
        self.load_attendance_records()
        self.load_attendance()

    def load_attendance(self):
        """โหลดข้อมูลการเข้าเรียนจากไฟล์ JSON ตามวันที่ปัจจุบัน"""
        today = datetime.date.today().strftime("%Y%m%d")
        attendance_file = LOCAL_DATA_DIR / f'attendance_{today}.json'
        
        if not attendance_file.exists():
            with open(attendance_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False, indent=4)
            logger.info(f"สร้างไฟล์ข้อมูลการเข้าเรียนใหม่ (JSON) สำหรับวันนี้: {today}")

        try:
            with open(attendance_file, 'r', encoding='utf-8') as f:
                self.attendance_records = json.load(f)
        except Exception as e:
            logger.error(f"ไม่สามารถโหลดข้อมูลการเข้าเรียน: {e}")
            self.attendance_records = {}
            
    def save_attendance(self):
        """บันทึกข้อมูลการเข้าเรียนลงในไฟล์ JSON ตามวันที่ปัจจุบัน"""
        today = datetime.date.today().strftime("%Y%m%d")
        attendance_file = LOCAL_DATA_DIR / f'attendance_{today}.json'
        try:
            with open(attendance_file, 'w', encoding='utf-8') as f:
                json.dump(self.attendance_records, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"ไม่สามารถบันทึกข้อมูลการเข้าเรียน: {e}")

    def mark_attendance(self, student_id):
        """ทำเครื่องหมายการเข้าเรียน"""
        current_time = int(time.time())
        self.attendance_records[student_id] = current_time
        self.save_attendance() # บันทึกข้อมูลการเข้าเรียน
        
    # ส่วนที่แก้ไขในคลาส AttendanceSystem เพื่อโหลดข้อมูลจาก CSV
    def load_student_data(self):
        """โหลดข้อมูลนักศึกษาจากไฟล์ CSV และอัพเดทไฟล์ JSON"""
        json_file = LOCAL_DATA_DIR / 'students.json'
        csv_file = Path('students.csv')
        
        # ตรวจสอบว่ามีไฟล์ CSV หรือไม่
        if csv_file.exists():
            try:
                # อ่านข้อมูลจาก CSV
                students = []
                with open(csv_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        students.append(row)
                
                # บันทึกข้อมูลลงในไฟล์ JSON
                with open(json_file, 'w', encoding='utf-8') as f:
                    json.dump(students, f, ensure_ascii=False, indent=4)
                
                # เก็บ student_ids
                self.student_ids = [student['id'] for student in students]
                logger.info(f"โหลดข้อมูลนักศึกษาจากไฟล์ CSV สำเร็จ: {len(self.student_ids)} คน")
                
            except Exception as e:
                logger.error(f"ไม่สามารถโหลดข้อมูลนักศึกษาจากไฟล์ CSV: {e}")
                # ถ้าเกิดข้อผิดพลาดให้โหลดจาก JSON ถ้ามี
                if json_file.exists():
                    try:
                        with open(json_file, 'r', encoding='utf-8') as f:
                            student_data = json.load(f)
                        self.student_ids = [student['id'] for student in student_data]
                        logger.info(f"โหลดข้อมูลนักศึกษาจากไฟล์ JSON สำรอง: {len(self.student_ids)} คน")
                    except Exception as e2:
                        logger.error(f"ไม่สามารถโหลดข้อมูลนักศึกษาจากไฟล์ JSON สำรอง: {e2}")
                        self.student_ids = []
                else:
                    self.student_ids = []
        else:
            # ถ้าไม่มีไฟล์ CSV ให้โหลดจาก JSON ถ้ามี
            if json_file.exists():
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        student_data = json.load(f)
                    self.student_ids = [student['id'] for student in student_data]
                    logger.info(f"โหลดข้อมูลนักศึกษาจากไฟล์ JSON สำเร็จ: {len(self.student_ids)} คน")
                except Exception as e:
                    logger.error(f"ไม่สามารถโหลดข้อมูลนักศึกษาจากไฟล์ JSON: {e}")
                    self.student_ids = []  # ค่าเริ่มต้น
            else:
                # สร้างไฟล์ CSV ตัวอย่าง
                try:
                    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow(['id', 'name', 'class'])
                        writer.writerow(['student_378', 'นายสมศรี มีใจ', '10301203'])
                        writer.writerow(['student_002', 'นางสาวอัศนีย์ ผิวดี', '10301203'])
                        writer.writerow(['student_402', 'นายใจดี มีไหม', '10301203'])
                    
                    # สร้างไฟล์ JSON จาก CSV ที่สร้างไว้
                    sample_data = [
                        {"id": "student_378", "name": "นายสมศรี มีใจ", "class": "10301203"},
                        {"id": "student_002", "name": "นางสาวอัศนีย์ ผิวดี", "class": "10301203"},
                        {"id": "student_402", "name": "นายใจดี มีไหม", "class": "10301203"}
                    ]
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(sample_data, f, ensure_ascii=False, indent=4)
                    
                    self.student_ids = ["student_378", "student_002", "student_402"]
                    logger.info("สร้างไฟล์ข้อมูลนักศึกษาตัวอย่าง (CSV และ JSON) สำเร็จ")
                except Exception as e:
                    logger.error(f"ไม่สามารถสร้างไฟล์ข้อมูลนักศึกษาตัวอย่าง: {e}")
                    self.student_ids = ["student_378", "student_002", "student_402"]  # ค่าเริ่มต้น

    def load_attendance_records(self):
        """โหลดข้อมูลการเช็คชื่อที่บันทึกไว้ในระบบ"""
        attendance_file = LOCAL_DATA_DIR / f'attendance_{datetime.date.today().strftime("%Y%m%d")}.json'
        
        if attendance_file.exists():
            try:
                with open(attendance_file, 'r', encoding='utf-8') as f:
                    self.attendance_records = json.load(f)
                    # แปลง string key กลับเป็น timestamp
                    self.checked_in_students = {student_id: int(timestamp) 
                                               for student_id, timestamp in self.attendance_records.items()}
                logger.info(f"โหลดข้อมูลการเช็คชื่อวันนี้สำเร็จ: {len(self.attendance_records)} รายการ")
            except Exception as e:
                logger.error(f"ไม่สามารถโหลดข้อมูลการเช็คชื่อ: {e}")
                self.attendance_records = {}
        else:
            self.attendance_records = {}

    def save_attendance_records(self):
        """บันทึกข้อมูลการเช็คชื่อลงไฟล์ JSON"""
        attendance_file = LOCAL_DATA_DIR / f'attendance_{datetime.date.today().strftime("%Y%m%d")}.json'
        
        try:
            with open(attendance_file, 'w', encoding='utf-8') as f:
                json.dump(self.attendance_records, f, ensure_ascii=False, indent=4)
            logger.info("บันทึกข้อมูลการเช็คชื่อสำเร็จ")
        except Exception as e:
            logger.error(f"ไม่สามารถบันทึกข้อมูลการเช็คชื่อ: {e}")

    # แก้ไขโดยเอาฟังก์ชันนี้กลับเข้ามาในคลาส และคอมเมนต์ออก
    def start_camera(self):
        """เริ่มต้นกล้อง"""
        try:
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                logger.error("ไม่สามารถเปิดกล้องได้")
                raise Exception("Camera could not be opened")
            logger.info("เปิดกล้องสำเร็จ")
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการเปิดกล้อง: {e}")
            raise
    
    # def start_camera(self, camera_source="rtsp://admin:378CsmjuThh@172.17.23.73:554/cam/realmonitor?channel=1&subtype=0"):
    #     """เริ่มต้นกล้องโดยใช้ RTSP"""
    #     try:
    #         self.cap = cv2.VideoCapture(camera_source)
    #         if not self.cap.isOpened():
    #             logger.error("ไม่สามารถเปิดกล้องได้ ตรวจสอบการเชื่อมต่อ RTSP")
    #             raise Exception(f"Camera stream could not be opened: {camera_source}")
    #         logger.info(f"เปิดกล้อง RTSP สำเร็จ (Source: {camera_source})")
    #     except Exception as e:
    #         logger.error(f"เกิดข้อผิดพลาดในการเปิดกล้อง: {e}")
    #         raise

    def compare_face(self, student_id, frame):
        """เปรียบเทียบใบหน้ากับภาพในฐานข้อมูล"""
        if not AWS_CONNECTED:
            logger.warning("ไม่สามารถเปรียบเทียบใบหน้าได้: ไม่ได้เชื่อมต่อ AWS")
            return False
        
        try:
            # ลดขนาดภาพเพื่อเพิ่มประสิทธิภาพ
            height, width = frame.shape[:2]
            if width > 640:
                scale = 640 / width
                frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
            
            # เปลี่ยนภาพเป็น bytes
            _, img_encoded = cv2.imencode('.jpg', frame)
            img_bytes = img_encoded.tobytes()

            # ส่งข้อมูลภาพไปยัง Rekognition
            response = rekognition.compare_faces(
                SourceImage={'Bytes': img_bytes},
                TargetImage={'S3Object': {'Bucket': self.s3_bucket, 'Name': f'students/{student_id}.jpg'}},
                SimilarityThreshold=self.similarity_threshold
            )

            logger.debug(f"Rekognition response: {response}")
            return len(response['FaceMatches']) > 0
        except rekognition.exceptions.InvalidS3ObjectException as e:
            logger.error(f"Error accessing S3 object for {student_id}: {e}")
            return False
        except rekognition.exceptions.InvalidParameterException as e:
            logger.error(f"Error comparing faces for {student_id}: Invalid parameter - {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error for {student_id}: {e}")
            return False

    def record_attendance(self, student_id):
        """บันทึกการเข้าเรียนลงฐานข้อมูล"""
        current_time = int(time.time())
        
        # ตรวจสอบว่าเช็คชื่อซ้ำหรือไม่
        if student_id in self.checked_in_students:
            last_checkin = self.checked_in_students[student_id]
            time_diff_minutes = (current_time - last_checkin) / 60
            
            if time_diff_minutes < self.duplicate_check_minutes:
                logger.info(f"{student_id} เช็คชื่อไปแล้วเมื่อ {time_diff_minutes:.1f} นาทีที่แล้ว")
                return False
        
        # บันทึกเวลาเช็คชื่อ
        self.checked_in_students[student_id] = current_time
        self.attendance_records[student_id] = current_time
        
        # บันทึกลงฐานข้อมูลท้องถิ่น
        self.save_attendance_records()
        
        # บันทึกลง DynamoDB ถ้าเชื่อมต่อ AWS ได้
        if AWS_CONNECTED:
            try:
                table.put_item(Item={
                    "student_id": student_id,
                    "timestamp": current_time,
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                logger.info(f"บันทึกการเช็คชื่อของ {student_id} ลง DynamoDB สำเร็จ")
            except Exception as e:
                logger.error(f"ไม่สามารถบันทึกลง DynamoDB: {e}")
        
        # เล่นเสียงแจ้งเตือน
        if SOUND_ENABLED and SOUND_SUCCESS:
            SOUND_SUCCESS.play()
        
        return True

    def process_frame(self, frame):
        """ประมวลผลเฟรมเพื่อตรวจจับและตรวจสอบใบหน้า"""
        if self.processing:
            return frame
        
        self.processing = True
        
        try:
            # คัดลอกเฟรมเพื่อไม่ให้กระทบกับการแสดงผล
            frame_copy = frame.copy()
            
            # ใช้ OpenCV ในการตรวจจับใบหน้า
            gray = cv2.cvtColor(frame_copy, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)

            if len(faces) == 0:
                logger.info("ไม่พบใบหน้า")
                # วาดข้อความบนภาพ
                cv2.putText(frame, "ไม่พบใบหน้า", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                           self.font_scale, (0, 0, 255), 2)
            else:
                # วาดกรอบรอบใบหน้าและตรวจสอบว่าเป็นนักศึกษาหรือไม่
                for (x, y, w, h) in faces:
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 0, 0), 2)  # วาดกรอบสีน้ำเงิน
                    roi = frame_copy[y:y + h, x:x + w]  # ส่วนของใบหน้าในกรอบ
                    
                    # วาดข้อความกำลังตรวจสอบ
                    cv2.putText(frame, "Scaning...", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 
                               0.5, (255, 0, 0), 2)
                    
                    # ตรวจสอบแต่ละนักศึกษา
                    for student_id in self.student_ids:
                        if self.compare_face(student_id, roi):
                            if self.record_attendance(student_id):
                                logger.info(f" {student_id} เช็คชื่อสำเร็จ!")
                                # วาดข้อความเช็คชื่อสำเร็จ
                                cv2.putText(frame, f"{student_id} เช็คชื่อสำเร็จ!", (x, y - 10), 
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                            else:
                                # กรณีเช็คชื่อซ้ำ
                                cv2.putText(frame, f"{student_id} เช็คชื่อไปแล้ว!", (x, y - 10), 
                                          cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 165, 0), 2)
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการประมวลผลเฟรม: {e}")
        finally:
            self.processing = False
            
        return frame

    def draw_ui_elements(self, frame):
        """วาดองค์ประกอบ UI บนเฟรม"""
        # แสดงเวลาปัจจุบัน
        current_time_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cv2.putText(frame, current_time_str, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                   self.font_scale, (0, 0, 0), 2)
        
        # แสดงสถานะการเชื่อมต่อ AWS
        connection_status = "Connect AWS: " + ("Online" if AWS_CONNECTED else "Offline")
        connection_color = (0, 255, 0) if AWS_CONNECTED else (0, 0, 255)
        cv2.putText(frame, connection_status, (frame.shape[1] - 300, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                   self.font_scale, connection_color, 2)
        
        # แสดงเวลาสแกนถัดไป
        next_scan = max(0, self.scan_interval - (time.time() - self.last_scan_time))
        cv2.putText(frame, f"Scan in: {next_scan:.1f} Sec.", (10, frame.shape[0] - 10), 
                   cv2.FONT_HERSHEY_SIMPLEX, self.font_scale, (0, 0, 255), 2)
        
        # แสดงรายชื่อนักศึกษาที่เช็คชื่อแล้ว
        y_offset = 70
        cv2.putText(frame, "Checked:", (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 
                   self.font_scale, (0, 0, 0), 2)
        y_offset += 30
        
        for i, (student_id, timestamp) in enumerate(self.checked_in_students.items()):
            if i >= 5:  # แสดงแค่ 5 คนล่าสุด
                cv2.putText(frame, f"... And {len(self.checked_in_students) - 5} People", 
                          (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, self.font_scale - 0.1, (0, 0, 0), 1)
                break
                
            checkin_time = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            cv2.putText(frame, f"{i+1}. {student_id} - {checkin_time}", (10, y_offset), 
                      cv2.FONT_HERSHEY_SIMPLEX, self.font_scale - 0.1, (0, 0, 0), 1)
            y_offset += 25
            
        return frame

    def run(self):
        """เริ่มการทำงานของระบบ"""
        try:
            self.start_camera()
            
            logger.info("เริ่มทำงานระบบเช็คชื่อ")
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            
            while self.running:
                current_time = time.time()
                ret, frame = self.cap.read()
                
                if not ret:
                    logger.error("ไม่สามารถอ่านเฟรมจากกล้อง")
                    break
                
                # ตรวจสอบว่าถึงเวลาสแกนหรือไม่
                if current_time - self.last_scan_time >= self.scan_interval:
                    self.last_scan_time = current_time
                    logger.info(f"กำลังสแกนที่เวลา: {datetime.datetime.fromtimestamp(current_time).strftime('%H:%M:%S')}")
                    
                    # สร้าง thread แยกสำหรับการประมวลผล
                    processing_thread = threading.Thread(
                        target=lambda: self.process_frame(frame),
                        daemon=True
                    )
                    processing_thread.start()
                
                # วาดองค์ประกอบ UI
                frame = self.draw_ui_elements(frame)
                
                # แสดงผลภาพจากกล้อง
                cv2.imshow(self.window_name, frame)
                
                # กด 'q' เพื่อออกจากโปรแกรม
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('s'):  # กด 's' เพื่อบันทึกภาพ
                    img_file = f"capture_{int(time.time())}.jpg"
                    cv2.imwrite(img_file, frame)
                    logger.info(f"บันทึกภาพลงในไฟล์ {img_file}")
                elif key == ord('r'):  # กด 'r' เพื่อรีเซ็ตการเช็คชื่อ
                    self.checked_in_students = {}
                    self.attendance_records = {}
                    self.save_attendance_records()
                    logger.info("รีเซ็ตข้อมูลการเช็คชื่อแล้ว")
                
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการรันระบบ: {e}")
        finally:
            if self.cap is not None:
                self.cap.release()
            cv2.destroyAllWindows()
            logger.info("ปิดระบบเช็คชื่อ")

# ฟังก์ชัน main
def main():
    # ตรวจสอบการมีอยู่ของไฟล์ .env
    if not os.path.exists('.env'):
        with open('.env', 'w') as f:
            f.write("# AWS Credentials\n")
            f.write("AWS_ACCESS_KEY_ID=Enter-Key\n")
            f.write("AWS_SECRET_ACCESS_KEY=Enter-Key\n")
        logger.info("สร้างไฟล์ .env สำหรับเก็บ credentials")
        
    # สร้างและรันระบบ
    system = AttendanceSystem()
    system.run()

if __name__ == "__main__":
    main()