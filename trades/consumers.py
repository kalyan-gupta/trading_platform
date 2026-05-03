import json
import logging
import uuid
import threading
from collections import defaultdict
from channels.generic.websocket import WebsocketConsumer
from django.contrib.auth.models import AnonymousUser
from .kotak_neo_api import KotakNeoAPI
from trading_platform.logging_utils import request_id_var, request_user_var

logger = logging.getLogger(__name__)

# Global state to track WebSocket connections per user
# Structure:
# {
#     user_id: {
#         "master_session": "ws_session_id",
#         "sessions": {
#             "ws_session_id": {
#                 "consumer": consumer_instance,
#                 "is_visible": True,
#                 "desired_subs": {'regular': set(), 'index': set(), 'depth': set()}
#             }
#         }
#     }
# }
USER_WS_STATE = defaultdict(lambda: {
    "master_session": None,
    "sessions": {}
})
ws_state_lock = threading.Lock()

class LiveQuotesConsumer(WebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api = None
        self.quote_cache = {}
        self.ws_session_id = None
        self.user_id = None
        self.ws_group_key = None
        
    def get_my_state(self):
        return USER_WS_STATE[self.ws_group_key]['sessions'].get(self.ws_session_id)

    def is_master(self):
        if not self.ws_group_key or not self.ws_session_id: return False
        return USER_WS_STATE[self.ws_group_key]['master_session'] == self.ws_session_id

    def connect(self):
        self.ws_session_id = f"WS-{str(uuid.uuid4())[:8]}"
        request_id_var.set(self.ws_session_id)
        
        user = self.scope.get('user', None)
        user_name = user.username if user and user.is_authenticated else "Anonymous"
        request_user_var.set(user_name)
        
        if not user or not hasattr(user, 'is_authenticated') or not user.is_authenticated:
            logger.warning(f"WebSocket connection rejected: Unauthenticated user.")
            self.close(code=4001)
            return

        self.user_id = user.id

        try:
            session_key = self.scope.get('session').session_key if self.scope.get('session') else None
            self.api = KotakNeoAPI(user=user, session_id=session_key)
            self.ws_group_key = f"{self.user_id}_{session_key}"
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            self.close(code=4002)
            return

        self.accept()
        auth_response = self.api.authenticate()
        if 'error' in auth_response:
            logger.error(f"WebSocket auth failure: {auth_response['error']}")
            self.send(text_data=json.dumps({
                'type': 'auth_failure',
                'message': auth_response['error']
            }))
            self.close()
        else:
            with ws_state_lock:
                USER_WS_STATE[self.ws_group_key]['sessions'][self.ws_session_id] = {
                    "consumer": self,
                    "is_visible": True,
                    "desired_subs": {'regular': set(), 'index': set(), 'depth': set()}
                }
                
                # If no master exists, we become master
                if USER_WS_STATE[self.ws_group_key]['master_session'] is None:
                    USER_WS_STATE[self.ws_group_key]['master_session'] = self.ws_session_id
                else:
                    # There is an existing master. If they are visible, we have a conflict.
                    # Send popup to *this* new connection.
                    master_id = USER_WS_STATE[self.ws_group_key]['master_session']
                    master_state = USER_WS_STATE[self.ws_group_key]['sessions'].get(master_id)
                    if master_state and master_state['is_visible']:
                        self.send(text_data=json.dumps({"type": "conflict_popup", "message": "Multiple active tabs"}))
                    else:
                        # Existing master is hidden, we take over safely
                        USER_WS_STATE[self.ws_group_key]['master_session'] = self.ws_session_id

            logger.info(f"WebSocket connected for user '{user_name}' (Master: {self.is_master()})")
            self.send(text_data=json.dumps({'message': 'Connected and authenticated'}))

    def disconnect(self, close_code):
        if not self.user_id or not self.ws_session_id: return
        
        with ws_state_lock:
            state = USER_WS_STATE[self.ws_group_key]
            
            # If we were master, clean up our Kotak subscriptions
            if state['master_session'] == self.ws_session_id:
                self.remove_all_subscriptions()
                state['master_session'] = None
                
                # See if another visible tab exists to take over
                visible_sessions = [sid for sid, info in state['sessions'].items() if info['is_visible'] and sid != self.ws_session_id]
                if visible_sessions:
                    new_master_id = visible_sessions[0]
                    state['master_session'] = new_master_id
                    new_master_consumer = state['sessions'][new_master_id]['consumer']
                    new_master_consumer.apply_all_subscriptions()
                    new_master_consumer.send(text_data=json.dumps({"type": "status", "message": "Feed resumed (active tab)"}))
            
            # Remove from tracking
            if self.ws_session_id in state['sessions']:
                del state['sessions'][self.ws_session_id]

    def apply_all_subscriptions(self):
        my_state = self.get_my_state()
        if not my_state or not self.api: return
        subs = my_state['desired_subs']
        
        if subs['regular']: self.api.subscribe([json.loads(t) for t in subs['regular']], on_message=self.on_quote, isIndex=False, isDepth=False)
        if subs['index']: self.api.subscribe([json.loads(t) for t in subs['index']], on_message=self.on_quote, isIndex=True, isDepth=False)
        if subs['depth']: self.api.subscribe([json.loads(t) for t in subs['depth']], on_message=self.on_quote, isIndex=False, isDepth=True)

    def remove_all_subscriptions(self):
        my_state = self.get_my_state()
        if not my_state or not self.api: return
        subs = my_state['desired_subs']
        
        if subs['regular']: self.api.unsubscribe([json.loads(t) for t in subs['regular']], isIndex=False, isDepth=False)
        if subs['index']: self.api.unsubscribe([json.loads(t) for t in subs['index']], isIndex=True, isDepth=False)
        if subs['depth']: self.api.unsubscribe([json.loads(t) for t in subs['depth']], isIndex=False, isDepth=True)

    def receive(self, text_data):
        request_id_var.set(self.ws_session_id)
        user = self.scope.get('user')
        request_user_var.set(user.username if user and user.is_authenticated else "Anonymous")
        
        try:
            text_data_json = json.loads(text_data)
            action = text_data_json.get('action')
            params = text_data_json.get('params', {})
            
            if action == 'subscribe':
                self.handle_subscribe(params, is_subscribe=True)
            elif action == 'unsubscribe':
                self.handle_subscribe(params, is_subscribe=False)
            elif action == 'set_visibility':
                self.handle_visibility(params.get('visible', True))
            elif action == 'claim_master':
                self.handle_claim_master()
            else:
                logger.warning(f"Unknown message type received: {action}")

        except json.JSONDecodeError:
            logger.error("Received non-JSON message")
        except Exception as e:
            logger.error(f"Error in receive method: {e}", exc_info=True)

    def handle_subscribe(self, params, is_subscribe):
        instruments = params.get('instrument_tokens', [])
        if not instruments: return
        
        isIndex = params.get('isIndex', False)
        isDepth = params.get('isDepth', False)
        
        with ws_state_lock:
            my_state = self.get_my_state()
            if not my_state: return
            
            subs = my_state['desired_subs']
            
            for token in instruments:
                str_token = json.dumps(token, sort_keys=True)
                if is_subscribe:
                    if not isDepth and not isIndex: subs['regular'].add(str_token)
                    if isIndex: subs['index'].add(str_token)
                    if isDepth: subs['depth'].add(str_token)
                else:
                    if not isDepth and not isIndex: subs['regular'].discard(str_token)
                    if isIndex: subs['index'].discard(str_token)
                    if isDepth: subs['depth'].discard(str_token)
            
            if self.is_master() and hasattr(self.api, 'subscribe'):
                if is_subscribe:
                    self.api.subscribe(instruments, on_message=self.on_quote, isIndex=isIndex, isDepth=False)
                    if isDepth:
                        self.api.subscribe(instruments, on_message=self.on_quote, isIndex=isIndex, isDepth=True)
                else:
                    self.api.unsubscribe(instruments, isIndex=isIndex, isDepth=False)
                    if isDepth:
                        self.api.unsubscribe(instruments, isIndex=isIndex, isDepth=True)

    def handle_visibility(self, is_visible):
        with ws_state_lock:
            state = USER_WS_STATE[self.ws_group_key]
            my_state = state['sessions'].get(self.ws_session_id)
            if not my_state: return
            
            my_state['is_visible'] = is_visible
            
            if not is_visible:
                if state['master_session'] == self.ws_session_id:
                    self.remove_all_subscriptions()
                    state['master_session'] = None
                    self.send(text_data=json.dumps({"type": "feed_paused", "message": "Feed paused (tab hidden)"}))
                    
                    # Try to elect a new master
                    visible_sessions = [sid for sid, info in state['sessions'].items() if info['is_visible']]
                    if len(visible_sessions) == 1:
                        new_master_id = visible_sessions[0]
                        state['master_session'] = new_master_id
                        new_master_consumer = state['sessions'][new_master_id]['consumer']
                        new_master_consumer.apply_all_subscriptions()
                        new_master_consumer.send(text_data=json.dumps({"type": "status", "message": "Feed resumed (active tab)"}))
            else:
                visible_sessions = [sid for sid, info in state['sessions'].items() if info['is_visible'] and sid != self.ws_session_id]
                if len(visible_sessions) > 0 or (state['master_session'] and state['master_session'] != self.ws_session_id):
                    self.send(text_data=json.dumps({"type": "conflict_popup", "message": "Multiple active tabs"}))
                else:
                    state['master_session'] = self.ws_session_id
                    self.apply_all_subscriptions()
                    self.send(text_data=json.dumps({"type": "status", "message": "Feed resumed (active tab)"}))

    def handle_claim_master(self):
        with ws_state_lock:
            state = USER_WS_STATE[self.ws_group_key]
            old_master_id = state['master_session']
            
            if old_master_id and old_master_id != self.ws_session_id:
                old_consumer = state['sessions'].get(old_master_id, {}).get('consumer')
                if old_consumer:
                    old_consumer.remove_all_subscriptions()
                    old_consumer.send(text_data=json.dumps({"type": "feed_paused", "message": "Feed paused. Active in another tab."}))
                    
            state['master_session'] = self.ws_session_id
            self.apply_all_subscriptions()
            self.send(text_data=json.dumps({"type": "status", "message": "Feed resumed (active tab)"}))

    def on_quote(self, quote):
        # We only send data if this consumer is the current master
        if not self.is_master():
            return
            
        request_id_var.set(self.ws_session_id)
        user = self.scope.get('user')
        request_user_var.set(user.username if user and user.is_authenticated else "Anonymous")
        
        try:
            normalized_list = []
            raw_data = []
            if isinstance(quote, dict):
                if quote.get('type') in ['stock_feed', 'depth_feed', 'index_feed'] and 'data' in quote:
                    raw_data = quote['data']
                else:
                    raw_data = [quote]
            elif isinstance(quote, list):
                raw_data = quote

            for item in raw_data:
                if not isinstance(item, dict): continue
                token = item.get('tk')
                if not token: continue
                
                if token not in self.quote_cache:
                    self.quote_cache[token] = {
                        'instrument_token': token,
                        'exchange_segment': item.get('e'),
                        'symbol': item.get('ts'),
                        'ltp': None, 'volume': None, 'open': None, 'high': None,
                        'low': None, 'close': None, 'atp': None, 'percent_change': None,
                        'depth': {
                            'buy': [{'price': None, 'quantity': None, 'orders': None} for _ in range(5)],
                            'sell': [{'price': None, 'quantity': None, 'orders': None} for _ in range(5)]
                        }
                    }
                
                cache = self.quote_cache[token]
                
                field_mappings = {
                    'ltp': ['lp', 'ltp', 'last_traded_price'], 'volume': ['v', 'volume'],
                    'open': ['o', 'open'], 'high': ['h', 'high'], 'low': ['lo', 'low'],
                    'close': ['c', 'close'], 'atp': ['ap', 'average_price'],
                    'percent_change': ['pc', 'net_change_percentage'], 'symbol': ['ts'], 'exchange_segment': ['e']
                }
                
                for canonical_field, raw_keys in field_mappings.items():
                    for k in raw_keys:
                        if k in item:
                            cache[canonical_field] = item[k]
                            break
                
                depth_keys = [
                    ('buy', 0, 'bp', 'bq', 'bno1'), ('buy', 1, 'bp1', 'bq1', 'bno2'),
                    ('buy', 2, 'bp2', 'bq2', 'bno3'), ('buy', 3, 'bp3', 'bq3', 'bno4'), ('buy', 4, 'bp4', 'bq4', 'bno5'),
                    ('sell', 0, 'sp', 'bs', 'sno1'), ('sell', 1, 'sp1', 'bs1', 'sno2'),
                    ('sell', 2, 'sp2', 'bs2', 'sno3'), ('sell', 3, 'sp3', 'bs3', 'sno4'), ('sell', 4, 'sp4', 'bs4', 'sno5'),
                ]
                
                for side, idx, p_key, q_key, o_key in depth_keys:
                    if item.get(p_key) is not None: cache['depth'][side][idx]['price'] = item.get(p_key)
                    if item.get(q_key) is not None: cache['depth'][side][idx]['quantity'] = item.get(q_key)
                    if item.get(o_key) is not None: cache['depth'][side][idx]['orders'] = item.get(o_key)

                if 'depth' in item:
                    d = item['depth']
                    for side in ['buy', 'sell']:
                        if side in d:
                            for idx, d_item in enumerate(d[side][:5]):
                                if d_item.get('price') is not None: cache['depth'][side][idx]['price'] = d_item.get('price')
                                if d_item.get('quantity') is not None: cache['depth'][side][idx]['quantity'] = d_item.get('quantity')
                                if d_item.get('orders') is not None: cache['depth'][side][idx]['orders'] = d_item.get('orders')
                
                quote_to_send = cache.copy()
                quote_to_send['request_type'] = item.get('request_type')
                normalized_list.append(quote_to_send)

            if normalized_list:
                self.send(text_data=json.dumps({'type': 'quote', 'data': normalized_list}))
        except Exception as e:
            logger.error(f"Error processing/sending quote to client: {e}", exc_info=True)
