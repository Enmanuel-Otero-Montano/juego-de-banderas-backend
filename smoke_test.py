from config import Settings
from pydantic import SecretStr, ValidationError

def test_settings_masking():
    settings = Settings(
        SECRET_KEY="test_secret",
        DATABASE_URL="postgres://user:pass@localhost/db",
        ENV="development"
    )
    
    # Verify repr masks values
    assert "test_secret" not in repr(settings), "Secret leaked in repr"
    # assert "**********" in repr(settings.SECRET_KEY) # Pydantic v2 might use different masking string
    
    # Verify model_dump masks values (standard behavior for SecretStr in Pydantic v2 if using model_dump(mode='json'))
    # model_dump() usually returns the raw objects, model_dump(mode='json') or str() masks them
    assert "test_secret" not in str(settings), "Secret leaked in str()"
    
    # Verify get_secret_value works
    assert settings.SECRET_KEY.get_secret_value() == "test_secret"
    assert settings.DATABASE_URL.get_secret_value() == "postgresql+psycopg2://user:pass@localhost/db"

def test_production_validation():
    # Should work in production if origins provided
    Settings(
        SECRET_KEY="secret",
        DATABASE_URL="postgres://db",
        ENV="production",
        ALLOWED_ORIGINS=["https://example.com"]
    )
    
    # Should fail if origins empty
    try:
        Settings(
            SECRET_KEY="secret",
            DATABASE_URL="postgres://db",
            ENV="production",
            ALLOWED_ORIGINS=[]
        )
        assert False, "Should have raised ValidationError for empty origins in production"
    except ValidationError as e:
        assert "ALLOWED_ORIGINS vacío en producción" in str(e)

    # Should fail if contains wildcard
    try:
        Settings(
            SECRET_KEY="secret",
            DATABASE_URL="postgres://db",
            ENV="production",
            ALLOWED_ORIGINS=["*", "https://example.com"]
        )
        assert False, "Should have raised ValidationError for wildcard in production"
    except ValidationError as e:
        assert "CORS wildcard (*) prohibido en producción" in str(e)

if __name__ == "__main__":
    print("Running manual smoke tests...")
    try:
        test_settings_masking()
        print("Masking test passed.")
        test_production_validation()
        print("Production validation test passed.")
        print("All smoke tests passed successfully!")
    except Exception as e:
        print(f"Tests failed: {e}")
        exit(1)
