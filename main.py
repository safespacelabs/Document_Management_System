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
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
from fastapi.responses import StreamingResponse


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


class FileMetadata(BaseModel):
    file_id: str
    username: str
    filename: str
    document_type: str
    document_list: str
    content_type: str
    file_size: int
    created_at: datetime
    updated_at: datetime
    tags: Optional[dict] = None

class MySQLStats(BaseModel):
    total_files: int
    files_per_user: dict
    files_per_type: dict
    total_storage: float  # in MB
    average_file_size: float  # in KB

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

@app.get("/mysql/stats", response_model=MySQLStats)
async def get_mysql_stats():
    """
    Get statistics about the stored file metadata from MySQL.
    Returns aggregated data about file counts, sizes, and distribution.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Get total number of files
        cursor.execute("SELECT COUNT(*) as total FROM file_metadata")
        total_files = cursor.fetchone()['total']
        
        # Get files per user
        cursor.execute("""
            SELECT username, COUNT(*) as file_count 
            FROM file_metadata 
            GROUP BY username
        """)
        files_per_user = {row['username']: row['file_count'] 
                         for row in cursor.fetchall()}
        
        # Get files per document type
        cursor.execute("""
            SELECT document_type, COUNT(*) as type_count 
            FROM file_metadata 
            GROUP BY document_type
        """)
        files_per_type = {row['document_type']: row['type_count'] 
                         for row in cursor.fetchall()}
        
        # Get storage statistics
        cursor.execute("""
            SELECT 
                SUM(file_size) as total_size,
                AVG(file_size) as avg_size
            FROM file_metadata
        """)
        size_stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        return MySQLStats(
            total_files=total_files,
            files_per_user=files_per_user,
            files_per_type=files_per_type,
            total_storage=round(size_stats['total_size'] / (1024 * 1024), 2),  # MB
            average_file_size=round(size_stats['avg_size'] / 1024, 2)  # KB
        )
        
    except Error as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Database error: {str(e)}"
        )

@app.get("/minio/stats")
async def get_minio_stats():
    """
    Get statistics about the stored files in MinIO.
    Returns information about bucket usage and object distribution.
    """
    try:
        # Get bucket statistics
        objects = list(minio_client.list_objects(BUCKET_NAME, recursive=True))
        
        # Calculate statistics
        total_objects = len(objects)
        total_size = sum(obj.size for obj in objects)
        
        # Group objects by prefix (username)
        user_stats = {}
        for obj in objects:
            username = obj.object_name.split('/')[0]
            if username not in user_stats:
                user_stats[username] = {
                    'file_count': 0,
                    'total_size': 0
                }
            user_stats[username]['file_count'] += 1
            user_stats[username]['total_size'] += obj.size
        
        return {
            "bucket_name": BUCKET_NAME,
            "total_objects": total_objects,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "user_statistics": user_stats
        }
        
    except S3Error as e:
        raise HTTPException(
            status_code=500, 
            detail=f"MinIO error: {str(e)}"
        )
@app.get("/files/metadata", response_model=List[FileMetadata])
async def get_all_files_metadata(
    username: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """
    Get metadata for all files with optional filtering.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        # Start with base query
        query = "SELECT * FROM file_metadata WHERE 1=1"
        params = []
        
        # Add username filter if provided
        if username:
            query += " AND username = %s"
            params.append(username)
            
        # Add document type filter if provided
        if document_type:
            query += " AND document_type = %s"
            params.append(document_type)
            
        # Add limit and offset
        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        # Print query and parameters for debugging
        print(f"Executing query: {query}")
        print(f"With parameters: {params}")
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # Convert datetime objects to strings to avoid JSON serialization issues
        for result in results:
            if 'created_at' in result:
                result['created_at'] = result['created_at'].isoformat()
            if 'updated_at' in result:
                result['updated_at'] = result['updated_at'].isoformat()
            # Convert tags from string to dict if it exists
            if 'tags' in result and isinstance(result['tags'], str):
                result['tags'] = json.loads(result['tags'])
        
        cursor.close()
        conn.close()
        
        return results
        
    except Exception as e:
        print(f"Error in get_all_files_metadata: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {str(e)}"
        )

@app.get("/files/download/batch/{username}")
async def batch_download_files(username: str):
    """
    Generate pre-signed URLs for all files belonging to a user.
    """
    try:
        # Get all files for the user from MySQL
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute(
            "SELECT * FROM file_metadata WHERE username = %s",
            (username,)
        )
        files = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        # Generate pre-signed URLs for each file
        download_urls = {}
        for file in files:
            object_path = get_object_path(
                username,
                file['document_list'],
                file['filename']
            )
            
            try:
                url = minio_client.get_presigned_url(
                    "GET",
                    BUCKET_NAME,
                    object_path,
                    expires=timedelta(hours=1)
                )
                download_urls[file['filename']] = {
                    'url': url,
                    'document_type': file['document_type'],
                    'document_list': file['document_list'],
                    'content_type': file['content_type'],
                    'file_size': file['file_size']
                }
            except S3Error as e:
                print(f"Error generating URL for {object_path}: {str(e)}")
                continue
        
        return {"download_urls": download_urls}
        
    except Error as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database error: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)