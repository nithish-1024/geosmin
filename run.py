import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("  GEOSMIN CLINICAL SAFETY PLATFORM - MVP SERVER")
    print("  Problem Statement: Healthcare PS 4 - Medication Safety")
    print("  Server URL: http://localhost:8000")
    print("  Swagger Docs: http://localhost:8000/docs")
    print("=" * 60)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
