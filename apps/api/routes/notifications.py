from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr
from apps.api.auth import current_user_id
from apps.api.desk import notifications
from apps.api.jobs.store import get_store
router = APIRouter()

class TelegramBody(BaseModel):
    bot_token: SecretStr = SecretStr('')
    chat_id: str
    enabled: bool = False

@router.get('/desk/notifications/telegram')
def status(user_id: str = Depends(current_user_id)):
    return notifications.status(user_id)

@router.put('/desk/notifications/telegram')
def configure(body: TelegramBody, user_id: str = Depends(current_user_id)):
    try:
        return notifications.configure(user_id,body.bot_token.get_secret_value(),body.chat_id,body.enabled)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503,str(exc)) from exc

@router.delete('/desk/notifications/telegram')
def remove(user_id: str = Depends(current_user_id)):
    return {'deleted':get_store().delete_user_key(user_id,'telegram')}
