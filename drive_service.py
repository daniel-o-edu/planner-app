import os.path
import io
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# Se alterar estes escopos, apague o arquivo token.json
SCOPES = ['https://www.googleapis.com/auth/drive.file']

FOLDER_NAME = "Planner_Backups"

class DriveService:
    def __init__(self):
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Gerencia o login do usuário via navegador (OAuth 2.0)."""
        # 1. Tenta carregar token salvo anteriormente
        if os.path.exists('token.json'):
            try:
                self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            except Exception:
                self.creds = None

        # 2. Se não tem token válido, pede login novo
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except Exception:
                    self.creds = None
            
            if not self.creds:
                if not os.path.exists('credentials.json'):
                    print("⚠️ AVISO: credentials.json (OAuth) não encontrado.")
                    return

                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        'credentials.json', SCOPES)
                    # Abre o navegador para você clicar em "Permitir"
                    self.creds = flow.run_local_server(port=0)
                except Exception as e:
                    print(f"⚠️ Erro na autenticação OAuth: {e}")
                    return

            # 3. Salva o token para a próxima vez
            with open('token.json', 'w') as token:
                token.write(self.creds.to_json())

        # 4. Constrói o serviço
        try:
            self.service = build('drive', 'v3', credentials=self.creds)
        except Exception as e:
            print(f"Erro ao conectar serviço Drive: {e}")

    def _get_or_create_folder(self):
        """Encontra ou cria a pasta usando a conta do próprio usuário."""
        if not self.service: return None

        try:
            # Busca a pasta pelo nome
            query = f"mimeType='application/vnd.google-apps.folder' and name='{FOLDER_NAME}' and trashed=false"
            results = self.service.files().list(q=query, fields="files(id)").execute()
            files = results.get('files', [])

            if files:
                return files[0]['id']
            else:
                # Cria a pasta (agora funciona pois é sua conta!)
                file_metadata = {
                    'name': FOLDER_NAME,
                    'mimeType': 'application/vnd.google-apps.folder'
                }
                folder = self.service.files().create(body=file_metadata, fields='id').execute()
                return folder.get('id')
        except Exception as e:
            print(f"Erro pasta: {e}")
            return None

    def upload_backup(self, filename, json_content):
        if not self.service: return False, "Serviço não autenticado"

        try:
            folder_id = self._get_or_create_folder()
            if not folder_id: return False, "Erro ao acessar pasta"

            file_metadata = {
                'name': filename,
                'parents': [folder_id]
            }
            
            fh = io.BytesIO(json_content.encode('utf-8'))
            media = MediaIoBaseUpload(fh, mimetype='application/json', resumable=True)
            
            self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            return True, "Backup salvo com sucesso!"
        except Exception as e:
            return False, str(e)

    def list_backups(self):
        if not self.service: return []
        try:
            folder_id = self._get_or_create_folder()
            if not folder_id: return []

            query = f"'{folder_id}' in parents and mimeType='application/json' and trashed=false"
            results = self.service.files().list(
                q=query, 
                pageSize=10, 
                orderBy="createdTime desc", 
                fields="files(id, name, createdTime, size)"
            ).execute()
            return results.get('files', [])
        except Exception as e:
            print(f"Erro listar: {e}")
            return []

    def download_file_content(self, file_id):
        if not self.service: return None
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            return fh.getvalue().decode('utf-8')
        except Exception as e:
            return None
