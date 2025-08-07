from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from bs4 import BeautifulSoup
import requests
from io import BytesIO
import uuid
import base64
from datetime import datetime, timedelta

app = FastAPI()
print("✅ FastAPI app starting...")

# Health check root route
@app.get("/")
def health():
    return {"message": "Backend running ✅"}

# Allow API access from any frontend (e.g., Expo app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token-based CAPTCHA store
captcha_store = {}

# Clean up expired tokens (older than 10 minutes)
def cleanup_expired_tokens():
    current_time = datetime.now()
    expired_tokens = []
    for token, data in captcha_store.items():
        if current_time - data["created_at"] > timedelta(minutes=10):
            expired_tokens.append(token)
    
    for token in expired_tokens:
        del captcha_store[token]

# ------------------ CAPTCHA ROUTE ------------------
@app.get("/get-captcha")
def get_captcha():
    # Clean up expired tokens
    cleanup_expired_tokens()
    
    session = requests.Session()
    base_url = "https://newerp.kluniversity.in"
    login_url = f"{base_url}/index.php?r=site%2Flogin"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    # Step 1: Get CSRF token
    res = session.get(login_url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    csrf = soup.find("meta", {"name": "csrf-token"})["content"]

    # Step 2: Trigger CAPTCHA
    dummy_data = {
        "_csrf": csrf,
        "LoginForm[username]": "",
        "LoginForm[password]": ""
    }
    res_post = session.post(login_url, data=dummy_data, headers=headers)
    soup_post = BeautifulSoup(res_post.text, "html.parser")

    # Step 3: Extract CAPTCHA URL
    captcha_img_tag = soup_post.find("img", src=lambda x: x and "r=site%2Fcaptcha" in x)
    if not captcha_img_tag:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "CAPTCHA not found"}
        )

    captcha_url = base_url + captcha_img_tag["src"].replace("&amp;", "&")
    captcha_response = session.get(captcha_url)
    
    # Step 4: Extract CAPTCHA text from the URL
    # The CAPTCHA text is usually in the URL parameters
    captcha_text = ""
    if "v=" in captcha_img_tag["src"]:
        captcha_text = captcha_img_tag["src"].split("v=")[1].split("&")[0]
    else:
        # If we can't extract from URL, we'll need to OCR or use a different approach
        # For now, let's try to get it from the session
        captcha_text = "TEMP"  # Placeholder - you might need to implement OCR here
    
    # Generate unique token
    token = str(uuid.uuid4())
    
    # Store CAPTCHA data with token
    captcha_store[token] = {
        "session": session,
        "csrf": csrf,
        "captcha_text": captcha_text,
        "created_at": datetime.now()
    }
    
    # Convert image to base64 for JSON response
    image_base64 = base64.b64encode(captcha_response.content).decode('utf-8')
    
    return JSONResponse(content={
        "success": True,
        "image": f"data:image/jpeg;base64,{image_base64}",
        "token": token
    })

# ------------------ LOGIN + FETCH TIMETABLE ------------------
@app.post("/fetch-timetable")
def fetch_timetable(
    username: str = Form(...),
    password: str = Form(...),
    captcha: str = Form(...),
    token: str = Form(...),
    academic_year_code: str = Form(default="19"),  # 2025–26
    semester_id: str = Form(default="1")  # Odd semester
):
    # Clean up expired tokens
    cleanup_expired_tokens()
    
    # Validate token
    if token not in captcha_store:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid or expired CAPTCHA token"}
        )
    
    captcha_data = captcha_store[token]
    session = captcha_data["session"]
    csrf = captcha_data["csrf"]
    stored_captcha = captcha_data["captcha_text"]
    
    # Validate CAPTCHA
    if captcha.lower() != stored_captcha.lower():
        # Remove the token after failed attempt
        del captcha_store[token]
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid CAPTCHA"}
        )
    
    base_url = "https://newerp.kluniversity.in"
    login_url = f"{base_url}/index.php?r=site%2Flogin"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    # Step 3: Login
    login_payload = {
        "_csrf": csrf,
        "LoginForm[username]": username,
        "LoginForm[password]": password,
        "LoginForm[captcha]": captcha,
    }

    login_response = session.post(login_url, data=login_payload, headers=headers)
    if "Logout" not in login_response.text:
        # Remove the token after failed attempt
        del captcha_store[token]
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Invalid credentials or captcha"}
        )

    # Step 4: Fetch timetable
    tt_url = f"{base_url}/index.php?r=timetables%2Funiversitymasteracademictimetableview%2Findividualstudenttimetableget&UniversityMasterAcademicTimetableView%5Bacademicyear%5D={academic_year_code}&UniversityMasterAcademicTimetableView%5Bsemesterid%5D={semester_id}"

    tt_response = session.get(tt_url, headers=headers)
    soup_tt = BeautifulSoup(tt_response.text, "html.parser")
    table = soup_tt.find("table")
    if not table:
        # Remove the token after failed attempt
        del captcha_store[token]
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Timetable not found"}
        )

    # Parse timetable
    thead = table.find("thead")
    headers = [th.text.strip() for th in thead.find_all("th")][1:]  # Skip 'Day'

    tbody = table.find("tbody")
    timetable = {}
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        day = cols[0].text.strip()
        slots = [td.text.strip() for td in cols[1:]]
        timetable[day] = dict(zip(headers, slots))

    # Remove the token after successful login
    del captcha_store[token]

    return {
        "success": True,
        "timetable": timetable
    }
