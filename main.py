from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from bs4 import BeautifulSoup
import requests
from io import BytesIO
import uuid
import base64
from datetime import datetime, timedelta
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="TimeTable Backend", version="1.0.0")

@app.on_event("startup")
async def startup_event():
    logger.info("✅ FastAPI app starting...")
    logger.info(f"Environment: PORT={os.getenv('PORT', '8000')}")

# Health check root route
@app.get("/")
def health():
    return {"message": "Backend running ✅", "status": "healthy"}

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
    try:
        current_time = datetime.now()
        expired_tokens = []
        for token, data in captcha_store.items():
            if current_time - data["created_at"] > timedelta(minutes=10):
                expired_tokens.append(token)
        
        for token in expired_tokens:
            del captcha_store[token]
        
        logger.info(f"Cleaned up {len(expired_tokens)} expired tokens")
    except Exception as e:
        logger.error(f"Error cleaning up tokens: {e}")

# ------------------ CAPTCHA ROUTE ------------------
@app.get("/get-captcha")
def get_captcha():
    try:
        # Clean up expired tokens
        cleanup_expired_tokens()
        
        session = requests.Session()
        base_url = "https://newerp.kluniversity.in"
        login_url = f"{base_url}/index.php?r=site%2Flogin"

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        # Step 1: Get CSRF token
        logger.info("Fetching CSRF token...")
        res = session.get(login_url, headers=headers, timeout=30)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        
        if not csrf_meta:
            logger.error("CSRF token not found in response")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "Failed to get CSRF token"}
            )
        
        csrf = csrf_meta["content"]

        # Step 2: Trigger CAPTCHA
        logger.info("Triggering CAPTCHA...")
        dummy_data = {
            "_csrf": csrf,
            "LoginForm[username]": "",
            "LoginForm[password]": ""
        }
        res_post = session.post(login_url, data=dummy_data, headers=headers, timeout=30)
        res_post.raise_for_status()
        
        soup_post = BeautifulSoup(res_post.text, "html.parser")

        # Step 3: Extract CAPTCHA URL
        captcha_img_tag = soup_post.find("img", src=lambda x: x and "r=site%2Fcaptcha" in x)
        if not captcha_img_tag:
            logger.error("CAPTCHA image not found")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": "CAPTCHA not found"}
            )

        captcha_url = base_url + captcha_img_tag["src"].replace("&amp;", "&")
        logger.info(f"Fetching CAPTCHA from: {captcha_url}")
        
        captcha_response = session.get(captcha_url, timeout=30)
        captcha_response.raise_for_status()
        
        # Step 4: Extract CAPTCHA text from the URL
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
        
        logger.info(f"Generated CAPTCHA token: {token[:8]}...")
        
        return JSONResponse(content={
            "success": True,
            "image": f"data:image/jpeg;base64,{image_base64}",
            "token": token
        })
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Network error while fetching CAPTCHA"}
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_captcha: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Internal server error"}
        )

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
    try:
        # Clean up expired tokens
        cleanup_expired_tokens()
        
        # Validate token
        if token not in captcha_store:
            logger.warning(f"Invalid token provided: {token[:8]}...")
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
            logger.warning(f"Invalid CAPTCHA provided for token: {token[:8]}...")
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
        logger.info(f"Attempting login for user: {username}")
        login_payload = {
            "_csrf": csrf,
            "LoginForm[username]": username,
            "LoginForm[password]": password,
            "LoginForm[captcha]": captcha,
        }

        login_response = session.post(login_url, data=login_payload, headers=headers, timeout=30)
        login_response.raise_for_status()
        
        if "Logout" not in login_response.text:
            # Remove the token after failed attempt
            del captcha_store[token]
            logger.warning(f"Login failed for user: {username}")
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "Invalid credentials or captcha"}
            )

        # Step 4: Fetch timetable
        logger.info(f"Fetching timetable for user: {username}")
        tt_url = f"{base_url}/index.php?r=timetables%2Funiversitymasteracademictimetableview%2Findividualstudenttimetableget&UniversityMasterAcademicTimetableView%5Bacademicyear%5D={academic_year_code}&UniversityMasterAcademicTimetableView%5Bsemesterid%5D={semester_id}"

        tt_response = session.get(tt_url, headers=headers, timeout=30)
        tt_response.raise_for_status()
        
        soup_tt = BeautifulSoup(tt_response.text, "html.parser")
        table = soup_tt.find("table")
        if not table:
            # Remove the token after failed attempt
            del captcha_store[token]
            logger.warning(f"Timetable not found for user: {username}")
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
        
        logger.info(f"Successfully fetched timetable for user: {username}")
        return {
            "success": True,
            "timetable": timetable
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error in fetch_timetable: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Network error while fetching timetable"}
        )
    except Exception as e:
        logger.error(f"Unexpected error in fetch_timetable: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Internal server error"}
        )
