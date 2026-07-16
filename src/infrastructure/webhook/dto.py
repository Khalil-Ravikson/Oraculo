from dataclasses import dataclass

@dataclass
class IncomingMessage:
    remote_jid: str       # "5598999999@s.whatsapp.net" ou "1234@g.us"
    sender_jid: str       # quem enviou (em grupos ≠ remote_jid)
    text: str
    push_name: str
    is_group: bool
    mentioned_bot: bool
    msg_key_id: str = ""
    has_media: bool = False
    media_type: str = ""