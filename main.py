import os
from decimal import Decimal
from typing import Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:5173')

if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError(
        'Missing Supabase env vars. Set SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY.'
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
auth_base_url = f"{SUPABASE_URL}/auth/v1"

app = FastAPI(title='KaritonPH API', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS.split(',') if origin.strip()],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class RegisterBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=1)


class CategoryCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CategoryUpdateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProductCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    category_id: int
    name: str = Field(min_length=1, max_length=180)
    price: Decimal = Field(gt=0)
    description: Optional[str] = None
    image_url: Optional[str] = None
    stock: int = Field(ge=0)


class ProductUpdateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    category_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=180)
    price: Optional[Decimal] = Field(default=None, gt=0)
    description: Optional[str] = None
    image_url: Optional[str] = None
    stock: Optional[int] = Field(default=None, ge=0)


class ProductPriceSettingsUpdateBody(BaseModel):
    is_price_disabled: bool


def _auth_headers(access_token: Optional[str] = None) -> dict:
    headers = {
        'apikey': SUPABASE_ANON_KEY,
        'Content-Type': 'application/json',
    }
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    return headers


def _raise_http_error(message: str, code: int = status.HTTP_400_BAD_REQUEST):
    raise HTTPException(status_code=code, detail=message)


async def _auth_request(method: str, endpoint: str, payload: Optional[dict] = None, token: Optional[str] = None):
    url = f'{auth_base_url}{endpoint}'
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, url, json=payload, headers=_auth_headers(token))

    if response.status_code >= 400:
        try:
            err = response.json()
        except Exception:
            err = {'message': response.text}
        detail = err.get('msg') or err.get('error_description') or err.get('message') or 'Auth request failed.'
        _raise_http_error(detail, response.status_code)

    return response.json() if response.text else {'success': True}


def _extract_token(authorization: Optional[str]) -> str:
    if not authorization:
        _raise_http_error('Missing Authorization header.', status.HTTP_401_UNAUTHORIZED)
    parts = authorization.split(' ')
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        _raise_http_error('Invalid Authorization header format.', status.HTTP_401_UNAUTHORIZED)
    return parts[1]


def _get_user_from_token(token: str) -> dict:
    user_resp = supabase.auth.get_user(token)
    user_data = getattr(user_resp, 'user', None)
    if not user_data:
        _raise_http_error('Invalid or expired token.', status.HTTP_401_UNAUTHORIZED)
    return user_data.model_dump()


def _get_auth_metadata_role(user_data: dict) -> Optional[str]:
    for metadata_key in ('app_metadata', 'user_metadata'):
        metadata = user_data.get(metadata_key) or {}
        role = metadata.get('role')
        if role in {'admin', 'user'}:
            return role
    return None


def _get_profile_seed(user_data: dict) -> dict:
    metadata = user_data.get('user_metadata') or {}
    display_name = metadata.get('name') or metadata.get('full_name') or user_data.get('email') or 'User'
    return {
        'id': user_data['id'],
        'name': display_name,
        'email': user_data.get('email') or '',
        'role': 'admin' if _get_auth_metadata_role(user_data) == 'admin' else 'user',
    }


def _get_or_create_profile(user_data: dict) -> dict:
    profile_resp = supabase.table('users').select('*').eq('id', user_data['id']).single().execute()
    if profile_resp.data:
        return profile_resp.data

    created = supabase.table('users').upsert(_get_profile_seed(user_data), on_conflict='id').execute()
    if created.data and len(created.data) > 0:
        return created.data[0]

    _raise_http_error('User profile not found.', status.HTTP_404_NOT_FOUND)


def _sync_profile_from_auth(user_data: dict, profile: dict) -> dict:
    updates = {}

    auth_email = user_data.get('email')
    if auth_email and profile.get('email') != auth_email:
        updates['email'] = auth_email

    if _get_auth_metadata_role(user_data) == 'admin' and profile.get('role') != 'admin':
        updates['role'] = 'admin'

    if not updates:
        return profile

    updated = (
        supabase.table('users')
        .update(updates)
        .eq('id', profile['id'])
        .execute()
    )
    if updated.data and len(updated.data) > 0:
        return updated.data[0]
    return profile


def _attach_profile_to_auth_result(result: dict) -> dict:
    access_token = result.get('access_token')
    if not access_token:
        return result

    user_data = _get_user_from_token(access_token)
    profile = _get_or_create_profile(user_data)
    profile = _sync_profile_from_auth(user_data, profile)
    return {
        **result,
        'profile': profile,
    }


def _admin_guard(profile: dict):
    if profile.get('role') != 'admin':
        _raise_http_error('Admin access required.', status.HTTP_403_FORBIDDEN)


async def get_current_user_profile(authorization: Optional[str] = Header(default=None)) -> dict:
    token = _extract_token(authorization)
    user_data = _get_user_from_token(token)
    profile = _get_or_create_profile(user_data)
    profile = _sync_profile_from_auth(user_data, profile)
    return {'auth_user': user_data, 'profile': profile, 'token': token}


@app.get('/health')
def health_check():
    return {'status': 'ok'}


@app.post('/api/login')
async def login(body: LoginBody):
    result = await _auth_request(
        'POST',
        '/token?grant_type=password',
        payload={'email': body.email, 'password': body.password},
    )
    return _attach_profile_to_auth_result(result)


@app.post('/api/logout')
async def logout(authorization: Optional[str] = Header(default=None)):
    token = _extract_token(authorization)
    await _auth_request('POST', '/logout', token=token)
    return {'message': 'Logged out successfully.'}


@app.post('/api/register')
async def register(body: RegisterBody):
    result = await _auth_request(
        'POST',
        '/signup',
        payload={'email': body.email, 'password': body.password, 'data': {'name': body.name}},
    )

    # Supabase returns user directly at root when email confirmation is enabled,
    # or nested under "user" when a session is immediately issued.
    user = result.get('user') or (result if result.get('id') else None)
    if user and user.get('id'):
        supabase.table('users').upsert(
            {'id': user['id'], 'name': body.name, 'email': body.email, 'role': 'user'},
            on_conflict='id',
        ).execute()

    return _attach_profile_to_auth_result(result)


@app.post('/api/refresh')
async def refresh(body: RefreshBody):
    result = await _auth_request(
        'POST',
        '/token?grant_type=refresh_token',
        payload={'refresh_token': body.refresh_token},
    )
    return _attach_profile_to_auth_result(result)


@app.get('/api/me')
async def me(current=Depends(get_current_user_profile)):
    return {
        'auth_user': current['auth_user'],
        'profile': current['profile'],
    }


@app.get('/api/settings/product-prices')
def get_product_price_settings():
    data = (
        supabase.table('product_settings')
        .select('is_price_disabled')
        .eq('id', 1)
        .maybe_single()
        .execute()
    )
    return {'is_price_disabled': bool((data.data or {}).get('is_price_disabled', False))}


@app.put('/api/settings/product-prices')
def update_product_price_settings(
    body: ProductPriceSettingsUpdateBody,
    current=Depends(get_current_user_profile),
):
    _admin_guard(current['profile'])
    updated = (
        supabase.table('product_settings')
        .upsert({'id': 1, 'is_price_disabled': body.is_price_disabled}, on_conflict='id')
        .execute()
    )
    if not updated.data:
        _raise_http_error('Product price settings could not be updated.', status.HTTP_500_INTERNAL_SERVER_ERROR)
    return {'is_price_disabled': bool(updated.data[0]['is_price_disabled'])}


@app.get('/api/products')
def list_products():
    data = supabase.table('products').select('*').order('created_at', desc=True).execute()
    return data.data


@app.get('/api/products/{product_id}')
def get_product(product_id: int):
    data = supabase.table('products').select('*').eq('id', product_id).single().execute()
    if not data.data:
        _raise_http_error('Product not found.', status.HTTP_404_NOT_FOUND)
    return data.data


@app.post('/api/products')
def create_product(body: ProductCreateBody, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    payload = body.model_dump(exclude_none=True)
    payload['price'] = str(payload['price'])
    created = supabase.table('products').insert(payload).execute()
    return created.data


@app.put('/api/products/{product_id}')
def update_product(product_id: int, body: ProductUpdateBody, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    payload = body.model_dump(exclude_none=True)
    if 'price' in payload:
        payload['price'] = str(payload['price'])
    updated = supabase.table('products').update(payload).eq('id', product_id).execute()
    return updated.data


@app.delete('/api/products/{product_id}')
def delete_product(product_id: int, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    deleted = supabase.table('products').delete().eq('id', product_id).execute()
    return deleted.data


@app.get('/api/categories')
def list_categories():
    data = supabase.table('categories').select('*').order('name').execute()
    return data.data


@app.post('/api/categories')
def create_category(body: CategoryCreateBody, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    created = supabase.table('categories').insert({'name': body.name}).execute()
    return created.data


@app.put('/api/categories/{category_id}')
def update_category(category_id: int, body: CategoryUpdateBody, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    updated = supabase.table('categories').update({'name': body.name}).eq('id', category_id).execute()
    return updated.data


@app.delete('/api/categories/{category_id}')
def delete_category(category_id: int, current=Depends(get_current_user_profile)):
    _admin_guard(current['profile'])
    deleted = supabase.table('categories').delete().eq('id', category_id).execute()
    return deleted.data


@app.get('/')
def root():
    return {'message': 'Hey Were Online!'}
