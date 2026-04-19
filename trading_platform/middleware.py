import time
import uuid
import logging
from trading_platform.logging_utils import request_id_var

logger = logging.getLogger('trading_platform.requests')

class RequestLoggingMiddleware:
    """
    Middleware to assign a unique UUID to each request and log its duration and status asynchronously.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Generate a unique request ID
        req_id = str(uuid.uuid4())
        
        # Set the request ID in the contextvar for this execution flow
        token = request_id_var.set(req_id)
        
        start_time = time.time()
        
        # Log the beginning of the request
        logger.info(f"Request Started: {request.method} {request.path}")
        
        try:
            # Process the request
            response = self.get_response(request)
            duration = time.time() - start_time
            
            # Log the successful completion
            logger.info(f"Request Finished: {request.method} {request.path} - Status: {response.status_code} - Duration: {duration:.3f}s")
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            # Log an error if the request failed before rendering a response
            logger.error(f"Request Failed: {request.method} {request.path} - Exception: {e} - Duration: {duration:.3f}s")
            raise
            
        finally:
            # Always reset the context variable back to its previous state
            request_id_var.reset(token)
