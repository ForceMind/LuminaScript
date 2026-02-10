import httpx
import asyncio
import sys

async def test_auth():
    base_url = "http://127.0.0.1:8000"
    
    # 1. Register
    username = "testuser_" + str(sys.version_info.minor)
    password = "secretpassword"
    
    print(f"Registering {username}...")
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{base_url}/auth/register", json={"username": username, "password": password})
            print(f"Register Status: {resp.status_code}")
            print(f"Register Response: {resp.text}")
        except Exception as e:
            print(f"Register failed to connect: {e}")
            return

        # 2. Login
        print(f"Logging in {username}...")
        try:
            # OAuth2PasswordRequestForm expects form data, not json
            resp = await client.post(f"{base_url}/token", data={"username": username, "password": password})
            print(f"Login Status: {resp.status_code}")
            print(f"Login Response: {resp.text}")
        except Exception as e:
            print(f"Login failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth())

