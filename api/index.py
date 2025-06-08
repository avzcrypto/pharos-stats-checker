import json
import requests

def handler(request):
    """Простая Vercel функция без Flask"""
    
    print(f"=== SIMPLE HANDLER START ===")
    print(f"Method: {request.method}")
    print(f"URL: {request.url}")
    
    # CORS headers
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json'
    }
    
    # Handle OPTIONS
    if request.method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'status': 'ok'})
        }
    
    # Handle health
    if 'health' in str(request.url):
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'status': 'ok', 'message': 'Simple API running'})
        }
    
    # Handle check-wallet
    if request.method == 'POST':
        try:
            print("Parsing request body...")
            
            # Parse body
            body = request.body
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            
            print(f"Body: {body}")
            data = json.loads(body)
            print(f"Parsed data: {data}")
            
            wallet_address = data.get('wallet_address', '').strip()
            
            # Simple validation
            if not wallet_address.startswith('0x') or len(wallet_address) != 42:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({
                        'success': False,
                        'error': 'Invalid wallet address'
                    })
                }
            
            print("Calling Pharos API...")
            result = call_pharos_api(wallet_address)
            print(f"API result: {result}")
            
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(result)
            }
            
        except Exception as e:
            print(f"Exception: {e}")
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({
                    'success': False,
                    'error': f'Server error: {str(e)}'
                })
            }
    
    return {
        'statusCode': 404,
        'headers': headers,
        'body': json.dumps({'error': 'Not found'})
    }

def call_pharos_api(wallet_address):
    """Call Pharos API"""
    try:
        api_base = "https://api.pharosnetwork.xyz"
        bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {bearer_token}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        print("Getting profile...")
        profile_response = requests.get(
            f"{api_base}/user/profile",
            params={'address': wallet_address},
            headers=headers,
            timeout=10
        )
        
        print("Getting tasks...")
        tasks_response = requests.get(
            f"{api_base}/user/tasks", 
            params={'address': wallet_address},
            headers=headers,
            timeout=10
        )
        
        if profile_response.status_code != 200:
            return {'success': False, 'error': 'Profile API failed'}
            
        if tasks_response.status_code != 200:
            return {'success': False, 'error': 'Tasks API failed'}
        
        profile_data = profile_response.json()
        tasks_data = tasks_response.json()
        
        # Process data
        total_points = profile_data.get('data', {}).get('user_info', {}).get('TotalPoints', 0)
        
        # Simple level calculation
        if total_points < 1000:
            level = 1
        elif total_points < 3000:
            level = 2
        else:
            level = 3
        
        return {
            'success': True,
            'address': wallet_address.lower(),
            'total_points': total_points,
            'current_level': level,
            'next_level': level + 1,
            'points_needed': 1000,
            'send_count': 0,
            'swap_count': 0,
            'lp_count': 0,
            'social_tasks': 0
        }
        
    except Exception as e:
        return {'success': False, 'error': f'API error: {str(e)}'}
