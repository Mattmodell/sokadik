from fastapi import FastAPI, APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import uuid
import logging
from pathlib import Path
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from bson import ObjectId

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# --- Config -----------------------------------------------------------------
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'change-me')
JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-secret')
JWT_ALGO = 'HS256'
APP_VERSION = '2026-06-gallery-fix-3'
JWT_EXPIRE_HOURS = 12

# --- DB ---------------------------------------------------------------------
client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
fs = AsyncIOMotorGridFSBucket(db, bucket_name='images')

# --- Models -----------------------------------------------------------------
class LoginIn(BaseModel):
    username: str
    password: str

class TokenOut(BaseModel):
    token: str
    expires_in: int

# --- Auth -------------------------------------------------------------------
oauth2 = OAuth2PasswordBearer(tokenUrl='/api/auth/login', auto_error=False)


def create_token(sub: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {'sub': sub, 'exp': exp, 'role': 'admin'}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def require_admin(token: Optional[str] = Depends(oauth2)) -> str:
    if not token:
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get('role') != 'admin':
            raise HTTPException(status_code=403, detail='Forbidden')
        return payload.get('sub')
    except JWTError:
        raise HTTPException(status_code=401, detail='Invalid or expired token')


# --- App --------------------------------------------------------------------
app = FastAPI(title='KSV-Sportkantine API')
api = APIRouter(prefix='/api')



@api.get('/uploads/{filename}')
async def get_upload(filename: str):
    doc = await db['images.files'].find_one({'filename': filename})
    if not doc:
        raise HTTPException(status_code=404, detail='Bild nicht gefunden')
    stream = await fs.open_download_stream(doc['_id'])
    data = await stream.read()
    ctype = (doc.get('metadata') or {}).get('content_type') or 'image/jpeg'
    return Response(content=data, media_type=ctype, headers={'Cache-Control': 'public, max-age=31536000'})


def _menu_out(doc: dict) -> dict:
    return {
        'id': doc['id'],
        'image_url': f"/api/uploads/{doc['filename']}",
        'uploaded_at': doc['uploaded_at'],
    }


CDN = 'https://cdn.website.dish.co/media/'
DEFAULT_GALLERY = [
    (CDN + '4b/e0/1383656/KSV-Sportkantine-ADD16DC2-0454-4222-83A7-86FD3C89FFC1.jpg', 'Willkommen in der Sportkantine'),
    (CDN + '8c/de/1383662/KSV-Sportkantine-D687E2D2-5760-4755-A169-1B8BEA0B2DFD.jpg', 'Bar KSV Kantine'),
    (CDN + 'cf/2c/1383672/KSV-Sportkantine-CEABE031-E0B0-46AA-83B7-742282C28A80.jpg', 'KSV Kantine'),
    (CDN + '3e/08/1338467/KSV-Sportkantine-8536CE71-5792-47F0-8422-73475C1A36E3.jpg', 'Sommersalat auf der Terrasse'),
    (CDN + '86/2a/1338462/KSV-Sportkantine-A0091A1C-41C9-4871-B7BC-27001F1EAAA0.jpg', 'Martinigansl'),
    (CDN + 'ee/89/1368493/KSV-Sportkantine-C7C6406D-6587-4125-8478-D755C753A4F2.jpg', 'Heringsschmaus'),
    (CDN + 'd9/8d/1383651/KSV-Sportkantine-6C07194B-D00B-48AE-A481-4DBA32DD5EAB.jpg', 'Jour Dessert'),
    (CDN + '36/2f/1368498/KSV-Sportkantine-9E88AE73-0620-4BFA-9EB9-A7FDCD2A99A1.jpg', 'Heringsschmaus'),
    (CDN + '60/e8/1383687/KSV-Sportkantine-42BE5B72-05C0-441A-8CE9-DC4DFF69628C.jpg', 'Gemütliche Gastgartenparty'),
    (CDN + '99/26/1404512/KSV-Sportkantine-0AE17D0C-BCDD-4912-AF01-07C41BE62521.jpg', 'Fingerfood'),
    (CDN + '31/73/1404517/KSV-Sportkantine-807B0220-80AA-484F-8FB2-0E31598C5B24.jpg', 'Leckeres vom Holzkohlengrill'),
    (CDN + 'e1/f0/1404527/KSV-Sportkantine-5BE3E35A-67B3-4F5B-A2BA-0B5FBB33CE3C.jpg', 'Fingerfood'),
]


# --- Public routes ----------------------------------------------------------
@api.get('/')
async def root():
    return {'name': 'KSV-Sportkantine API', 'status': 'ok'}


@api.post('/auth/login', response_model=TokenOut)
async def login(data: LoginIn):
    if data.username != ADMIN_USERNAME or data.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail='Falsche Zugangsdaten')
    token = create_token(data.username)
    return TokenOut(token=token, expires_in=JWT_EXPIRE_HOURS * 3600)


MENU_COLLECTIONS = {'weekly-menu': 'weekly_menu', 'seasonal-menu': 'seasonal_menu'}
ALLOWED_EXT = ('.jpg', '.jpeg', '.png', '.webp')


def _check_image(file: UploadFile):
    ext = os.path.splitext(file.filename or '')[1].lower()
    if not (file.content_type or '').startswith('image/') and ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail='Nur Bilddateien erlaubt')


async def _save_file(file: UploadFile, prefix: str) -> tuple:
    ext = os.path.splitext(file.filename or 'img.jpg')[1].lower()
    if ext not in ALLOWED_EXT:
        ext = '.jpg'
    new_id = str(uuid.uuid4())
    filename = f"{prefix}_{new_id}{ext}"
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail='Datei zu groß (max. 8 MB)')
    await fs.upload_from_stream(filename, data, metadata={'content_type': file.content_type})
    return new_id, filename


async def _remove_file(filename: Optional[str]):
    if filename:
        async for f in fs.find({'filename': filename}):
            await fs.delete(f._id)


def _collection(kind: str):
    if kind not in MENU_COLLECTIONS:
        raise HTTPException(status_code=404, detail='Nicht gefunden')
    return db[MENU_COLLECTIONS[kind]]


async def _clear(coll):
    for d in await coll.find({}).to_list(1000):
        await _remove_file(d.get('filename'))
    await coll.delete_many({})


def _gallery_out(doc: dict) -> dict:
    url = doc.get('url') or (f"/api/uploads/{doc['filename']}" if doc.get('filename') else '')
    ts = doc.get('uploaded_at')
    return {'id': str(doc.get('id') or doc.get('_id')), 'image_url': url, 'title': doc.get('title') or '',
            'uploaded_at': ts.isoformat() if isinstance(ts, datetime) else str(ts or '')}


def _sort_key(doc: dict):
    ts = doc.get('uploaded_at')
    ts = ts.replace(tzinfo=timezone.utc) if isinstance(ts, datetime) and ts.tzinfo is None else ts
    epoch = ts.timestamp() if isinstance(ts, datetime) else 0
    order = doc.get('order') if isinstance(doc.get('order'), (int, float)) else 0
    return (order, -epoch)


@api.get('/version')
async def version():
    return {'version': APP_VERSION}


@api.get('/gallery')
async def get_gallery():
    try:
        docs = await db.gallery.find({}).to_list(1000)
    except Exception:
        logger.exception('Gallery query failed')
        return []
    out = []
    for d in sorted(docs, key=_sort_key):
        try:
            item = _gallery_out(d)
            if item['image_url']:
                out.append(item)
        except Exception:
            logger.exception('Skipping broken gallery doc %s', d.get('_id'))
    return out


@api.get('/{kind}')
async def get_menu(kind: str):
    doc = await _collection(kind).find_one(sort=[('uploaded_at', -1)])
    return _menu_out(doc) if doc else None


# --- Admin routes -----------------------------------------------------------
@api.post('/admin/gallery')
async def upload_gallery(files: List[UploadFile] = File(...), _: str = Depends(require_admin)):
    for f in files:
        _check_image(f)
    out = []
    for f in files:
        new_id, filename = await _save_file(f, 'gallery')
        doc = {'id': new_id, 'filename': filename, 'title': '', 'order': 0,
               'uploaded_at': datetime.now(timezone.utc)}
        await db.gallery.insert_one(doc)
        out.append(_gallery_out(doc))
    return out


@api.delete('/admin/gallery/{image_id}')
async def delete_gallery(image_id: str, _: str = Depends(require_admin)):
    doc = await db.gallery.find_one({'id': image_id})
    if not doc and ObjectId.is_valid(image_id):
        doc = await db.gallery.find_one({'_id': ObjectId(image_id)})
    if not doc:
        raise HTTPException(status_code=404, detail='Bild nicht gefunden')
    await _remove_file(doc.get('filename'))
    await db.gallery.delete_one({'_id': doc['_id']})
    return {'ok': True}


@api.post('/admin/{kind}')
async def upload_menu(kind: str, file: UploadFile = File(...), _: str = Depends(require_admin)):
    coll = _collection(kind)
    _check_image(file)
    await _clear(coll)
    new_id, filename = await _save_file(file, kind.replace('-', '_'))
    doc = {'id': new_id, 'filename': filename, 'uploaded_at': datetime.now(timezone.utc)}
    await coll.insert_one(doc)
    return _menu_out(doc)


@api.delete('/admin/{kind}')
async def delete_menu(kind: str, _: str = Depends(require_admin)):
    await _clear(_collection(kind))
    return {'ok': True}


async def seed_gallery():
    if await db.settings.find_one({'key': 'gallery_seeded'}):
        return
    now = datetime.now(timezone.utc)
    for i, (url, title) in enumerate(DEFAULT_GALLERY):
        await db.gallery.insert_one({'id': str(uuid.uuid4()), 'url': url, 'title': title,
                                     'order': i + 1, 'uploaded_at': now})
    await db.settings.insert_one({'key': 'gallery_seeded'})


# --- Wire it up -------------------------------------------------------------
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@app.on_event('startup')
async def startup():
    await seed_gallery()
    logger.info('KSV backend started. Admin user: %s', ADMIN_USERNAME)


@app.on_event('shutdown')
async def shutdown_db_client():
    client.close()
