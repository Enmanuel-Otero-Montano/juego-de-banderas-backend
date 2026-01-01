import requests
import uuid
import time
import sys

BASE_URL = "http://localhost:8000"

from db import database, models

def verify_user_in_db(username):
    db_session = database.SessionLocal()
    try:
        user = db_session.query(models.User).filter(models.User.username == username).first()
        if user:
            user.is_verified = True
            user.country = "UY"
            db_session.commit()
            print(f"Manually verified user {username} and set country to UY")
    finally:
        db_session.close()

def register_user():
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    password = "password123"
    email = f"{username}@example.com"
    
    # Create dummy image
    dummy_image = (b"fakeimagebytes", "test.png", "image/png")
    
    resp = requests.post(
        f"{BASE_URL}/register", 
        data={
            "username": username,
            "password": password,
            "email": email,
            "full_name": "Test User",
            "country": "UY"
        },
        files={"profile_image": dummy_image}
    )
    if resp.status_code != 200:
        print(f"Register failed: {resp.text}")
        sys.exit(1)

    # Manually verify user
    verify_user_in_db(username)
    
    # Login
    
    # Login
    auth_resp = requests.post(f"{BASE_URL}/token", data={
        "username": username,
        "password": password
    })
    if auth_resp.status_code != 200:
        print(f"Login failed: {auth_resp.text}")
        sys.exit(1)
        
    token = auth_resp.json()["access_token"]
    return token, username

def verify():
    print("Starting verification...")
    try:
        token, username = register_user()
    except Exception as e:
        print(f"Setup failed: {e}")
        return

    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Post score for "Career" (no region, default)
    # Actually, front uses url.
    print("\n--- Posting Career Score ---")
    resp = requests.post(f"{BASE_URL}/scores/", json={
        "score": 100,
        "game_mode": "career",
        "game_region": "https://banderas.com/game", # Should normalize to 'career'
        "game_duration_seconds": 120
    }, headers=headers)
    print(f"Post Career: {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text)
    
    # 2. Post score for "America"
    print("\n--- Posting America Score ---")
    resp = requests.post(f"{BASE_URL}/scores/", json={
        "score": 200,
        "game_mode": "career",
        "game_region": "https://banderas.com/game/america", # Should normalize to 'america'
        "game_duration_seconds": 120
    }, headers=headers)
    print(f"Post America: {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text)
    
    # 3. Check /me/position global (should be based on Career or aggregation?)
    # My impl: scope=global returns 'career' ranking.
    print("\n--- Checking Global Position ---")
    resp = requests.get(f"{BASE_URL}/scores/me/position?scope=global", headers=headers)
    print(f"Global Pos: {resp.status_code}, {resp.json()}")
    res_global = resp.json()
    if res_global.get('rank') is None:
        print("FAIL: Global rank is None")
    
    # My impl matches global=career -> max_score should be 100
    if res_global.get('max_score') != 100:
        print(f"FAIL: Expected max_score 100 (career), got {res_global.get('max_score')}")
    else:
        print("PASS: Global score correct")
    
    # 4. Check /me/position region=america
    print("\n--- Checking America Position ---")
    resp = requests.get(f"{BASE_URL}/scores/me/position?scope=region&region=america", headers=headers)
    print(f"America Pos: {resp.status_code}, {resp.json()}")
    res_america = resp.json()
    if res_america.get('max_score') != 200:
        print(f"FAIL: Expected max_score 200 (america), got {res_america.get('max_score')}")
    else:
        print("PASS: America score correct")

    # 4b. Check /me/position country (should use UY from register)
    print("\n--- Checking Country Position ---")
    resp = requests.get(f"{BASE_URL}/scores/me/position?scope=country", headers=headers)
    print(f"Country Pos: {resp.status_code}, {resp.json()}")
    # Country uses 'career' score by default in my impl
    if resp.json().get('max_score') != 100:
         print(f"FAIL: Expected max_score 100 (country career), got {resp.json().get('max_score')}")

    # 5. Check Summary
    print("\n--- Checking Summary ---")
    resp = requests.get(f"{BASE_URL}/scores/summary", headers=headers)
    if resp.status_code != 200:
        print(f"FAIL: Summary error {resp.text}")
    else:
        data = resp.json()
        print(f"Summary keys: {data.keys()}")
        # Check structure
        if 'global_top' in data and 'user_positions' in data:
            print("PASS: Summary structure valid")
            print("User Best:", data['user_best'])
            
            # user_best should be career best (100)
            if data['user_best']['max_score'] != 100:
                print(f"FAIL: Summary user_best is {data['user_best']['max_score']}, expected 100")
        else:
            print("FAIL: Missing keys in summary")

if __name__ == "__main__":
    verify()
