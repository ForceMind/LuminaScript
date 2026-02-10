import sys
try:
    from passlib.context import CryptContext
    print("Has passlib")
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    print("Context created")
    h = pwd_context.hash("test")
    print(f"Hash created: {h}")
    v = pwd_context.verify("test", h)
    print(f"Verify: {v}")
except Exception as e:
    import traceback
    traceback.print_exc()
