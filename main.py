from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from enum import Enum
from minio import Minio
from minio.error import S3Error
import os
import io
import uuid
import mysql.connector
from mysql.connector import Error
from datetime import datetime, timedelta
from typing import Dict, List
import json
import time

app = FastAPI(title="I-9 Document Management System")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Constants
MAX_FILE_SIZE = 400 * 1024  # 400KB in bytes
BUCKET_NAME = "i9-documents"


# MySQL Configuration
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "mysql"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "hritamdey@61"),
    "database": os.getenv("MYSQL_DATABASE", "i9_documents"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "connect_timeout": 60,
    "charset": 'utf8mb4',
    "use_unicode": True,
    "auth_plugin": 'mysql_native_password',
    "raise_on_warnings": True
}

# MinIO client initialization
minio_client = Minio(
    os.getenv("MINIO_HOST", "localhost:9000"),
    access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
    secure=False
)

class DocumentType(str, Enum):
    PASSPORT = "passport"
    RESIDENT_CARD = "residentcard"
    DRIVERS_LICENSE = "driverslicense"
    SCHOOL_ID = "schoolid"
    SSN_CARD = "ssncard"
    BIRTH_CERT = "birthcert"

DOCUMENT_LISTS = {
    DocumentType.PASSPORT: "list_a",
    DocumentType.RESIDENT_CARD: "list_a",
    DocumentType.DRIVERS_LICENSE: "list_b",
    DocumentType.SCHOOL_ID: "list_b",
    DocumentType.SSN_CARD: "list_c",
    DocumentType.BIRTH_CERT: "list_c"
}

ALLOWED_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}

def init_minio():
    try:
        # Add retry logic for MinIO connection
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                # Check if bucket exists
                found = minio_client.bucket_exists(BUCKET_NAME)
                if not found:
                    print(f"Creating bucket {BUCKET_NAME}")
                    minio_client.make_bucket(BUCKET_NAME)
                    
                    # Set bucket policy
                    policy = {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": "*"},
                                "Action": ["s3:GetBucketLocation"],
                                "Resource": [f"arn:aws:s3:::{BUCKET_NAME}"]
                            },
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": "*"},
                                "Action": ["s3:GetObject"],
                                "Resource": [f"arn:aws:s3:::{BUCKET_NAME}/*"]
                            }
                        ]
                    }
                    
                    policy_str = json.dumps(policy)
                    minio_client.set_bucket_policy(BUCKET_NAME, policy_str)
                    print(f"Bucket {BUCKET_NAME} created and configured successfully")
                else:
                    print(f"Bucket {BUCKET_NAME} already exists")
                break
                
            except Exception as e:
                print(f"Attempt {retry_count + 1}/{max_retries} failed: {str(e)}")
                retry_count += 1
                time.sleep(5)  # Wait 5 seconds before retrying
                
        if retry_count >= max_retries:
            print("Failed to initialize MinIO after maximum retries")
            
    except Exception as e:
        print(f"Error occurred while initializing MinIO: {str(e)}")
        raise

# Database initialization function
def init_database():
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        # Create metadata table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                file_id VARCHAR(36) PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                document_type VARCHAR(50) NOT NULL,
                document_list VARCHAR(50) NOT NULL,
                content_type VARCHAR(100) NOT NULL,
                file_size INT NOT NULL,
                tags JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_username (username),
                INDEX idx_document_type (document_type)
            )
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        print("Database initialized successfully")
        
    except Error as e:
        print(f"Error initializing database: {e}")
        raise HTTPException(status_code=500, detail="Database initialization failed")

@app.on_event("startup")
async def startup_event():
    print("Initializing MinIO...")
    init_minio()
    print("Initializing Database...")
    init_database()

def validate_file_extension(filename: str) -> bool:
    extension = os.path.splitext(filename)[1].lower()
    return extension in ALLOWED_EXTENSIONS

def get_content_type(filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    content_types = {
        '.pdf': 'application/pdf',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png'
    }
    return content_types.get(extension, 'application/octet-stream')

async def validate_file_size(file: UploadFile):
    try:
        content = await file.read()
        file_size = len(content)
        await file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds maximum limit of 400KB. Current size: {file_size / 1024:.1f}KB"
            )
        return True
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

def get_document_list(document_type: str) -> str:
    try:
        doc_type = DocumentType(document_type)
        return DOCUMENT_LISTS[doc_type]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document type")

def get_object_path(username: str, doc_list: str, filename: str) -> str:
    return f"{username}/{doc_list}/{filename}"

async def store_metadata(
    file_id: str,
    username: str,
    filename: str,
    document_type: str,
    doc_list: str,
    content_type: str,
    file_size: int,
    tags: Dict = None
):
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO file_metadata 
            (file_id, username, filename, document_type, document_list, 
             content_type, file_size, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        cursor.execute(
            query,
            (
                file_id,
                username,
                filename,
                document_type,
                doc_list,
                content_type,
                file_size,
                json.dumps(tags) if tags else None
            )
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
    except Error as e:
        print(f"Error storing metadata: {e}")
        raise HTTPException(status_code=500, detail="Failed to store metadata")

async def delete_metadata(username: str, document_type: str):
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            DELETE FROM file_metadata 
            WHERE username = %s AND document_type = %s
        """
        
        cursor.execute(query, (username, document_type))
        conn.commit()
        cursor.close()
        conn.close()
        
    except Error as e:
        print(f"Error deleting metadata: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete metadata")

@app.post("/upload/{username}/{document_type}")
async def upload_document(
    username: str,
    document_type: str,
    file: UploadFile = File(...)
):
    print(f"Received upload request for user {username}, document type {document_type}")
    try:
        # First, ensure bucket exists
        if not minio_client.bucket_exists(BUCKET_NAME):
            init_minio()  # Try to create bucket if it doesn't exist
            
        if not validate_file_extension(file.filename):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Allowed types: .pdf, .jpg, .jpeg, .png"
            )
        
        await validate_file_size(file)
        content = await file.read()
        file_size = len(content)
        
        doc_list = get_document_list(document_type)
        extension = os.path.splitext(file.filename)[1]
        new_filename = f"{document_type}{extension}"
        object_path = get_object_path(username, doc_list, new_filename)
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        
        # Delete existing files and metadata
        try:
            prefix = f"{username}/{doc_list}/{document_type}"
            objects = minio_client.list_objects(BUCKET_NAME, prefix=prefix)
            for obj in objects:
                minio_client.remove_object(BUCKET_NAME, obj.object_name)
            await delete_metadata(username, document_type)
        except S3Error as e:
            print(f"Error cleaning up existing files: {e}")
        
        # Upload new file
        content_type = get_content_type(file.filename)
        try:
            minio_client.put_object(
                BUCKET_NAME,
                object_path,
                io.BytesIO(content),
                len(content),
                content_type=content_type
            )
        except S3Error as e:
            print(f"Error uploading to MinIO: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file: {str(e)}"
            )
        
        # Store metadata
        tags = {
            "document_category": doc_list,
            "original_filename": file.filename
        }
        
        await store_metadata(
            file_id=file_id,
            username=username,
            filename=new_filename,
            document_type=document_type,
            doc_list=doc_list,
            content_type=content_type,
            file_size=file_size,
            tags=tags
        )
        
        return JSONResponse(
            content={
                "message": "Document uploaded successfully",
                "file_name": new_filename,
                "file_id": file_id
            },
            status_code=200
        )
        
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Unexpected error during upload: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete/{username}/{document_type}")
async def delete_document(username: str, document_type: str):
    try:
        doc_list = get_document_list(document_type)
        prefix = f"{username}/{doc_list}/{document_type}"
        
        # Find and delete the file from MinIO
        deleted = False
        objects = minio_client.list_objects(BUCKET_NAME, prefix=prefix)
        for obj in objects:
            minio_client.remove_object(BUCKET_NAME, obj.object_name)
            deleted = True
        
        # Delete metadata from MySQL
        await delete_metadata(username, document_type)
        
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"No document found for type: {document_type}"
            )
        
        return JSONResponse(
            content={"message": "Document deleted successfully"},
            status_code=200
        )
        
    except S3Error as e:
        raise HTTPException(status_code=500, detail=f"Storage error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/view/{username}")
async def view_documents(username: str):
    try:
        documents: Dict[str, List[str]] = {
            "list_a": [],
            "list_b": [],
            "list_c": []
        }
        
        # Get metadata from MySQL
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT * FROM file_metadata 
            WHERE username = %s 
            ORDER BY created_at DESC
        """
        
        cursor.execute(query, (username,))
        metadata_records = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Organize documents by list
        for record in metadata_records:
            list_type = record['document_list']
            if list_type in documents:
                documents[list_type].append({
                    'filename': record['filename'],
                    'document_type': record['document_type'],
                    'file_id': record['file_id'],
                    'created_at': record['created_at'].isoformat(),
                    'file_size': record['file_size'],
                    'tags': json.loads(record['tags']) if record['tags'] else None
                })
        
        return JSONResponse(
            content={"documents": documents},
            status_code=200
        )
        
    except Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{username}/{list_type}/{filename}")
async def get_download_url(username: str, list_type: str, filename: str):
    try:
        object_path = get_object_path(username, list_type, filename)
        # Generate presigned URL valid for 1 hour
        url = minio_client.get_presigned_url(
            "GET",
            BUCKET_NAME,
            object_path,
            expires=timedelta(hours=1)
        )
        return JSONResponse(
            content={"download_url": url},
            status_code=200
        )
    except S3Error as e:
        raise HTTPException(status_code=500, detail=f"Storage error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {"message": "I-9 Document Management System API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)