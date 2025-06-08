from flask import Flask, request, jsonify
import requests
import json

app = Flask(__name__)

# Pharos API configuration
API_BASE = "https://api.pharosnetwork.xyz"
BEARER_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"

def add_cors_headers(response):
    """Add CORS headers to response"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

@app.after_request
def after_request(response):
    return add_cors_headers(response)

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'message': 'Pharos Stats API is running'})

@app.route('/api/check-wallet', methods=['POST', 'OPTIONS'])
def check_wallet():
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Debug: print request info
        print(f"Request method: {request.method}")
        print(f"Request headers: {dict(request.headers)}")
        print(f"Request data: {request.data}")
        
        # Try different ways to parse JSON
        try:
            data = request.get_json()
            if not data:
                data = request.get_json(force=True)
        except Exception as json_error:
            print(f"JSON parsing error: {json_error}")
            try:
                import json
                data = json.loads(request.data.decode('utf-8'))
            except Exception as fallback_error:
                print(f"Fallback JSON parsing error: {fallback_error}")
                return jsonify({
                    'success': False,
                    'error': 'Invalid JSON data'
                }), 400
        
        print(f"Parsed JSON: {data}")
        
        if not data or 'wallet_address' not in data:
            print("Missing wallet_address in request")
            return jsonify({
                'success': False,
                'error': 'wallet_address is required'
            }), 400
        
        wallet_address = data['wallet_address'].strip()
        
        # Validate address
        if not validate_address(wallet_address):
            return jsonify({
                'success': False,
                'error': 'Invalid wallet address format'
            }), 400
        
        # Get data from Pharos API
        result = get_pharos_data(wallet_address)
        
        if result.get('success'):
            return jsonify(result)
        else:
            return jsonify(result), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

def validate_address(address):
    """Validate Ethereum address"""
    if not address or len(address) != 42 or not address.startswith('0x'):
        return False
    try:
        int(address[2:], 16)
        return True
    except:
        return False

def get_pharos_data(wallet_address):
    """Get data from Pharos API"""
    try:
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Authorization': f'Bearer {BEARER_TOKEN}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Get profile data
        profile_response = requests.get(
            f"{API_BASE}/user/profile",
            params={'address': wallet_address},
            headers=headers,
            timeout=15
        )
        
        # Get tasks data
        tasks_response = requests.get(
            f"{API_BASE}/user/tasks",
            params={'address': wallet_address},
            headers=headers,
            timeout=15
        )
        
        if profile_response.status_code != 200:
            return {'success': False, 'error': 'Failed to fetch profile data'}
        
        if tasks_response.status_code != 200:
            return {'success': False, 'error': 'Failed to fetch tasks data'}
        
        profile_data = profile_response.json()
        tasks_data = tasks_response.json()
        
        if profile_data.get('code') != 0:
            return {'success': False, 'error': 'Invalid profile response'}
        
        if tasks_data.get('code') != 0:
            return {'success': False, 'error': 'Invalid tasks response'}
        
        # Parse data
        user_info = profile_data.get('data', {}).get('user_info', {})
        total_points = user_info.get('TotalPoints', 0)
        
        user_tasks = tasks_data.get('data', {}).get('user_tasks', [])
        stats = parse_tasks(user_tasks)
        
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
            'send_count': stats['send_count'],
            'swap_count': stats['swap_count'],
            'lp_count': stats['lp_count'],
            'social_tasks': stats['social_tasks']
        }
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

def parse_tasks(user_tasks):
    """Parse tasks data"""
    stats = {'send_count': 0, 'swap_count': 0, 'lp_count': 0, 'social_tasks': 0}
    
    for task in user_tasks:
        task_id = task.get('TaskId', 0)
        complete_times = task.get('CompleteTimes', 0)
        
        if task_id == 103:
            stats['send_count'] = complete_times
        elif task_id == 101:
            stats['swap_count'] = complete_times
        elif task_id == 102:
            stats['lp_count'] = complete_times
        elif task_id in [201, 202, 203, 204]:
            stats['social_tasks'] += 1
    
    return stats

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

# Vercel entry point
if __name__ != '__main__':
    # For Vercel
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
