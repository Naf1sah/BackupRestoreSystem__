import os
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build #Google API Client

# === CONFIG ===
CREDENTIALS_FILE = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\credentials.json" #Diundur dari Google Cloud Console
TOKEN_FILE = r"D:\PENS 2025\Semester 6\Kegiatan Nafisah\PROJECT TA\Data\BackupSystemRestore\token.json" 

# Folder ID dari RawData (sesuai config kamu)
RAW_FOLDER_ID = "1Q603DmHehooE1JV6Xhe36qLU3oLdYZN3"

# Scope: akses metadata + baca file
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

def main():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    service = build("drive", "v3", credentials=creds) #Drive API v3

    # Query: semua file di folder RawData
    query = f"'{RAW_FOLDER_ID}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get("files", [])

    if not items:
        print("Tidak ada file di folder RawData.")
    else:
        print("Isi folder RawData:")
        for item in items:
            print(f"- {item['name']} (id: {item['id']})")

if __name__ == "__main__":
    main()
