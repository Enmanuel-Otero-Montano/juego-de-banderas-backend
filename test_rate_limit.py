import httpx
import asyncio

async def test_rate_limit():
    async with httpx.AsyncClient() as client:
        base_url = "http://127.0.0.1:8000"
        
        # Test 1: Registro (debe fallar después de 5 intentos)
        print("Testing /register rate limit (5/hour)...")
        for i in range(7):
            response = await client.post(
                f"{base_url}/register",
                data={
                    "username": f"test{i}",
                    "full_name": f"Test User {i}",
                    "email": f"test{i}@example.com",
                    "password": "testpass123"
                },
                files={"profile_image": ("test.png", b"fake_image_data", "image/png")}
            )
            print(f"  Intento {i+1}: Status {response.status_code}")
            if response.status_code == 429:
                print(f"  ✅ Rate limit activado correctamente: {response.json()}")
                break
        
        # Test 2: Login (debe fallar después de 10 intentos)
        print("\nTesting /login rate limit (10/minute)...")
        for i in range(12):
            response = await client.post(
                f"{base_url}/login",
                data={"username": "test", "password": "wrong"}
            )
            print(f"  Intento {i+1}: Status {response.status_code}")
            if response.status_code == 429:
                print(f"  ✅ Rate limit activado correctamente: {response.json()}")
                break

if __name__ == "__main__":
    asyncio.run(test_rate_limit())
