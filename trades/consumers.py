import json
import logging
from channels.generic.websocket import WebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from .kotak_neo_api import KotakNeoAPI

logger = logging.getLogger(__name__)

class LiveQuotesConsumer(WebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api = None

    def connect(self):
        user = self.scope.get('user', None)
        if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
            self.close(code=4001)
            return

        try:
            self.api = KotakNeoAPI(user=user)
        except Exception as e:
            self.close(code=4002)
            return

        self.accept()
        auth_response = self.api.authenticate()
        if 'error' in auth_response:
            self.send(text_data=json.dumps({'error': auth_response['error']}))
            self.close()
        else:
            self.send(text_data=json.dumps({'message': 'Connected and authenticated'}))

    def disconnect(self, close_code):
        if hasattr(self.api, 'unsubscribe'):
            self.api.unsubscribe() # Assuming there's a method to clean up the subscription

    def receive(self, text_data):
        try:
            text_data_json = json.loads(text_data)
            action = text_data_json.get('action')
            params = text_data_json.get('params', {})
            
            if action == 'subscribe':
                instruments = params.get('instrument_tokens')
                isIndex = params.get('isIndex', False)
                isDepth = params.get('isDepth', False)
                if instruments:
                    self.api.subscribe(instruments, on_message=self.on_quote, isIndex=isIndex, isDepth=isDepth)
            elif action == 'unsubscribe':
                instruments = params.get('instrument_tokens')
                isIndex = params.get('isIndex', False)
                isDepth = params.get('isDepth', False)
                if instruments:
                    self.api.unsubscribe(instruments, isIndex=isIndex, isDepth=isDepth)
            else:
                logger.warning(f"Unknown message type received: {action}")

        except json.JSONDecodeError:
            logger.error("Received non-JSON message")
        except Exception as e:
            logger.error(f"Error in receive method: {e}", exc_info=True)

    def on_quote(self, quote):
        """Callback function to handle incoming quotes from the API."""
        try:
            # Forward the quote to the connected client
            self.send(text_data=json.dumps({
                'type': 'quote',
                'data': quote
            }))
        except Exception as e:
            logger.error(f"Error sending quote to client: {e}", exc_info=True)
