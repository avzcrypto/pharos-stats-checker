import json
import requests

def handler(request):
    """Vercel serverless function handler"""
    
    # Handle CORS
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Content-Type': 'application/json'
    }
    
    # Handle OPTIONS request
    if request.method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'status': 'ok'})
        }
    
    # Handle health check
    if request.url.path == '/api/health':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({'status': 'ok', 'message': 'API is running'})
        }
    
    # Handle wallet check
    if request.url.path == '/api/check-wallet' and request.method == 'POST':
        try:
            # Parse request body
            body = request.body
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            data = json.loads(body)
            
            wallet_address = data.get('wallet_address', '').strip()
            
            if not wallet_address:
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({
                        'success': False,
                        'error': 'wallet_address is required'
                    })
                }
            
            # Validate address
            if not (wallet_address.startswith('0x') and len(wallet_address) == 42):
                return {
                    'statusCode': 400,
                    'headers': headers,
                    'body': json.dumps({
                        'success': False,
                        'error': 'Invalid wallet address format'
                    })
                }
            
            # Call Pharos API
            result = get_pharos_data(wallet_address)
            
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(result)
            }
            
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps({
                    'success': False,
                    'error': f'Server error: {str(e)}'
                })
            }
    
    # Default response
    return {
        'statusCode': 404,
        'headers': headers,
        'body': json.dumps({'error': 'Not found'})
    }

def get_pharos_data(wallet_address):
    """Get data from Pharos API"""
    try:
        api_base = "https://api.pharosnetwork.xyz"
        bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {bearer_token}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Get profile data
        profile_response = requests.get(
            f"{api_base}/user/profile",
            params={'address': wallet_address},
            headers=headers,
            timeout=15
        )
        
        # Get tasks data
        tasks_response = requests.get(
            f"{api_base}/user/tasks",
            params={'address': wallet_address},
            headers=headers,
            timeout=15
        )
        
        if profile_response.status_code != 200 or tasks_response.status_code != 200:
            return {
                'success': False,
                'error': 'Failed to fetch data from Pharos API'
            }
        
        profile_data = profile_response.json()
        tasks_data = tasks_response.json()
        
        if profile_data.get('code') != 0 or tasks_data.get('code') != 0:
            return {
                'success': False,
                'error': 'Invalid response from Pharos API'
            }
        
        # Parse profile data
        user_info = profile_data.get('data', {}).get('user_info', {})
        total_points = user_info.get('TotalPoints', 0)
        
        # Parse tasks data
        user_tasks = tasks_data.get('data', {}).get('user_tasks', [])
        send_count = 0
        swap_count = 0
        lp_count = 0
        social_tasks = 0
        
        for task in user_tasks:
            task_id = task.get('TaskId', 0)
            complete_times = task.get('CompleteTimes', 0)
            
            if task_id == 103:
                send_count = complete_times
            elif task_id == 101:
                swap_count = complete_times
            elif task_id == 102:
                lp_count = complete_times
            elif task_id in [201, 202, 203, 204]:
                social_tasks += 1
        
        # Calculate level
        current_level = calculate_level(total_points)
        next_level = current_level + 1
        points_for_next = calculate_points_for_level(next_level)
        points_needed = max(0, points_for_next - total_points)
        
        return {
            'success': True,
            'address': wallet_address.lower(),
            'total_points': total_points,
            'current_level': current_level,
            'next_level': next_level,
            'points_needed': points_needed,
            'send_count': send_count,
            'swap_count': swap_count,
            'lp_count': lp_count,
            'social_tasks': social_tasks
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error processing data: {str(e)}'
        }

def calculate_level(points):
    """Calculate level based on points"""
    if points < 1000:
        return 1
    elif points < 3000:
        return 2
    elif points < 6000:
        return 3
    elif points < 10000:
        return 4
    elif points < 15000:
        return 5
    elif points < 25000:
        return 6
    elif points < 40000:
        return 7
    elif points < 60000:
        return 8
    elif points < 90000:
        return 9
    else:
        return 10

def calculate_points_for_level(level):
    """Calculate points needed for level"""
    levels = {
        1: 0, 2: 1000, 3: 3000, 4: 6000, 5: 10000, 
        6: 15000, 7: 25000, 8: 40000, 9: 60000, 10: 90000, 11: 150000
    }
    return levels.get(level, 150000)
