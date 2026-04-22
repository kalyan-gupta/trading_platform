from django.http import JsonResponse
from django.db.models import Max
from .models import BasketOrder
from .decorators import ajax_login_required
from .kotak_neo_api import KotakNeoAPI
import json
import logging

logger = logging.getLogger(__name__)

@ajax_login_required
def add_to_basket_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        
        # Get max sort_order for user
        max_order = BasketOrder.objects.filter(user=request.user).aggregate(Max('sort_order'))['sort_order__max']
        next_order = (max_order or 0) + 1
        
        basket_item = BasketOrder.objects.create(
            user=request.user,
            instrument_token=data.get('instrument_token'),
            exchange_segment=data.get('exchange_segment'),
            trading_symbol=data.get('trading_symbol'),
            quantity=int(data.get('quantity')),
            price=float(data.get('price', 0)),
            transaction_type=data.get('transaction_type'),
            product_type=data.get('product_type'),
            order_type=data.get('order_type', 'L'),
            sort_order=next_order
        )
        
        logger.info(f"User '{request.user.username}' added {basket_item.trading_symbol} to basket.")
        return JsonResponse({
            'status': 'success',
            'message': f"Added {basket_item.trading_symbol} to basket.",
            'item_id': basket_item.id
        })
    except Exception as e:
        logger.error(f"Error adding to basket: {e}")
        return JsonResponse({'error': str(e)}, status=400)

@ajax_login_required
def get_basket_ajax(request):
    orders = BasketOrder.objects.filter(user=request.user).values()
    return JsonResponse({'status': 'success', 'basket': list(orders)})

@ajax_login_required
def remove_from_basket_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        order_id = data.get('order_id')
        BasketOrder.objects.filter(user=request.user, id=order_id).delete()
        return JsonResponse({'status': 'success', 'message': 'Item removed from basket.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@ajax_login_required
def clear_basket_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
    
    try:
        BasketOrder.objects.filter(user=request.user).delete()
        return JsonResponse({'status': 'success', 'message': 'Basket cleared successfully.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@ajax_login_required
def update_basket_sequence_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        sequence = data.get('sequence', []) # List of {id, sort_order}
        
        for item in sequence:
            BasketOrder.objects.filter(user=request.user, id=item['id']).update(sort_order=item['sort_order'])
            
        return JsonResponse({'status': 'success', 'message': 'Basket sequence updated.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

@ajax_login_required
def execute_basket_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
    
    orders = BasketOrder.objects.filter(user=request.user).order_by('sort_order', 'created_at')
    if not orders.exists():
        return JsonResponse({'error': 'Basket is empty.'}, status=400)
    
    try:
        api = KotakNeoAPI(user=request.user, session_id=request.session.session_key)
    except Exception as e:
        return JsonResponse({'error': f"Failed to initialize API: {str(e)}"}, status=400)
    
    results = []
    failed = False
    error_message = ""
    
    for order in orders:
        logger.info(f"Executing basket order: {order}")
        
        try:
            # Place the trade
            api_response = api.place_trade(
                trading_symbol=order.trading_symbol,
                quantity=order.quantity,
                price=order.price if order.order_type == 'L' else 0,
                transaction_type=order.transaction_type,
                exchange_segment=order.exchange_segment,
                product=order.product_type,
                order_type=order.order_type
            )
            
            if isinstance(api_response, dict) and 'error' in api_response:
                failed = True
                error_message = api_response['error']
                results.append({'id': order.id, 'status': 'error', 'message': error_message})
                break
            
            if 'errMsg' in api_response:
                failed = True
                error_message = api_response['errMsg']
                results.append({'id': order.id, 'status': 'error', 'message': error_message})
                break
            
            # Success: Remove from basket and continue
            results.append({'id': order.id, 'status': 'success', 'order_id': api_response.get('nOrdNo', 'N/A')})
            order.delete()
            
        except Exception as e:
            failed = True
            error_message = str(e)
            results.append({'id': order.id, 'status': 'error', 'message': error_message})
            break
            
    if failed:
        return JsonResponse({
            'status': 'partial_failure',
            'message': f"Execution stopped at {order.trading_symbol}: {error_message}",
            'results': results
        }, status=400)
    
    return JsonResponse({
        'status': 'success',
        'message': f"All {len(results)} orders in basket executed successfully.",
        'results': results
    })
