I-9 Document Management System
A secure document management system for handling I-9 verification documents, built with FastAPI, MinIO, MySQL, and Docker.
Features

Secure Document Storage: Upload and manage I-9 verification documents
Multiple Document Categories:

List A: Passport, Resident Card
List B: Driver's License, School ID
List C: Social Security Card, Birth Certificate


File Type Support: PDF, JPG, JPEG, PNG
Size Limit: 400KB per file
User-Specific Storage: Separate document management for each user
Document Versioning: Automatic handling of document updates
Secure Storage: MinIO object storage with access control
Database Tracking: MySQL database for document metadata

Technology Stack

Backend: FastAPI (Python)
Storage: MinIO (S3-compatible object storage)
Database: MySQL 8.0
Frontend: HTML/CSS/JavaScript
Web Server: Nginx
Containerization: Docker & Docker Compose

Prerequisites

Docker Desktop installed and running
Basic knowledge of Docker and Docker Compose
Port availability: 3000, 8000, 9000, 9001, 3307

Project Structure
Copyi9-system/
├── docker-compose.yml      # Docker services configuration
├── Dockerfile             # Backend service build instructions
├── requirements.txt       # Python dependencies
├── main.py               # FastAPI backend application
├── index.html            # Frontend interface
├── nginx.conf            # Nginx configuration
└── README.md             # Project documentation
Installation & Setup



Environment Setup

Ensure Docker Desktop is running
Verify required ports are available (3000, 8000, 9000, 9001, 3307)


Build and Start Services
bashCopydocker compose up --build -d

Verify Services
bashCopydocker compose ps


Service URLs

Frontend Interface: http://localhost:3000
Backend API: http://localhost:8000
MinIO Console: http://localhost:9001

Username: minioadmin
Password: minioadmin


API Documentation: http://localhost:8000/docs

API Endpoints

POST /upload/{username}/{document_type}: Upload a document
DELETE /delete/{username}/{document_type}: Delete a document
GET /view/{username}: View all documents for a user
GET /download/{username}/{list_type}/{filename}: Get document download URL

Document Categories

List A Documents (Identity and Employment Authorization)

U.S. Passport
Permanent Resident Card


List B Documents (Identity)

Driver's License
School ID Card


List C Documents (Employment Authorization)

Social Security Card
Birth Certificate



File Requirements

Supported Formats: PDF, JPG, JPEG, PNG
Maximum File Size: 400KB
Naming Convention: Automatically handled by the system

Development
To make changes to the application:

Stop the containers:
bashCopydocker compose down

Make your code changes
Rebuild and start:
bashCopydocker compose up --build -d


Monitoring and Troubleshooting
View Logs
bashCopy# All services
docker compose logs

# Specific service
docker compose logs backend
docker compose logs minio
docker compose logs mysql
docker compose logs frontend
Check Container Status
bashCopydocker compose ps
Check Port Usage
bashCopy# Windows PowerShell
Get-NetTCPConnection -LocalPort 8000
Get-NetTCPConnection -LocalPort 9000
Get-NetTCPConnection -LocalPort 9001
Get-NetTCPConnection -LocalPort 3000
Get-NetTCPConnection -LocalPort 3307
Common Issues and Solutions




Upload Failures

Verify file size (max 400KB)
Check file type (PDF, JPG, JPEG, PNG)
Review browser console for errors
Check backend logs for detailed error messages


Database Connection Issues

Verify MySQL container is running
Check MySQL logs for errors
Verify connection settings in main.py


MinIO Access Problems

Check MinIO container status
Verify credentials in docker-compose.yml
Ensure bucket permissions are correct



Security Notes
This configuration is intended for development purposes. For production deployment:

Change Default Credentials

Update MinIO credentials
Update MySQL root password
Implement proper user authentication


Enable HTTPS

Add SSL/TLS certificates
Update Nginx configuration
Secure all service endpoints


Implement Access Controls

Add user authentication
Implement role-based access
Add document access logging


Regular Backups

Set up MySQL backups
Configure MinIO backup strategy
Implement monitoring and alerts