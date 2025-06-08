from http.server import BaseHTTPRequestHandler
import json
import requests
import urllib.parse

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == '/api/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = json.dumps({'status': 'ok', 'message': 'API is running'})
            self.wfile.write(response.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        print(f"=== POST REQUEST START ===")
        print(f"Path: {self.path}")
        
        if self.path == '/api/check-wallet':
            try:
                # Read request body
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                print(f"Raw data: {post_data}")
                
                # Parse JSON
                data = json.loads(post_data.decode('utf-8'))
                print(f"Parsed data: {data}")
                
                wallet_address = data.get('wallet_address', '').strip()
                print(f"Wallet address: {wallet_address}")
                
                # Validate address
                if not wallet_address.startswith('0x') or len(wallet_address) != 42:
                    self.send_error_response({'success': False, 'error': 'Invalid wallet address'}, 400)
                    return
                
                print("Calling Pharos API...")
                result = self.call_pharos_api(wallet_address)
                print(f"API result: {result}")
                
                if result.get('success'):
                    self.send_success_response(result)
                else:
                    self.send_error_response(result, 400)
                    
            except Exception as e:
                print(f"Exception: {e}")
                import traceback
                traceback.print_exc()
                self.send_error_response({
                    'success': False, 
                    'error': f'Server error: {str(e)}'
                }, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def send_success_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response = json.dumps(data)
        self.wfile.write(response.encode())

    def send_error_response(self, data, status_code):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        response = json.dumps(data)
        self.wfile.write(response.encode())

    def call_pharos_api(self, wallet_address):
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
                timeout=15
            )
            
            print("Getting tasks...")
            tasks_response = requests.get(
                f"{api_base}/user/tasks", 
                params={'address': wallet_address},
                headers=headers,
                timeout=15
            )
            
            if profile_response.status_code != 200:
                return {'success': False, 'error': f'Profile API failed: {profile_response.status_code}'}
                
            if tasks_response.status_code != 200:
                return {'success': False, 'error': f'Tasks API failed: {tasks_response.status_code}'}
            
            profile_data = profile_response.json()
            tasks_data = tasks_response.json()
            
            print(f"Profile response: {profile_data}")
            print(f"Tasks response: {tasks_data}")
            
            if profile_data.get('code') != 0:
                return {'success': False, 'error': 'Invalid profile response'}
                
            if tasks_data.get('code') != 0:
                return {'success': False, 'error': 'Invalid tasks response'}
            
            # Process data
            user_info = profile_data.get('data', {}).get('user_info', {})
            total_points = user_info.get('TotalPoints', 0)
            
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
            if total_points < 1000:
                current_level = 1
            elif total_points < 3000:
                current_level = 2
            elif total_points < 6000:
                current_level = 3
            elif total_points < 10000:
                current_level = 4
            elif total_points < 15000:
                current_level = 5
            elif total_points < 25000:
                current_level = 6
            elif total_points < 40000:
                current_level = 7
            elif total_points < 60000:
                current_level = 8
            elif total_points < 90000:
                current_level = 9
            else:
                current_level = 10
            
            next_level = current_level + 1
            
            # Calculate points needed
            levels = {1: 0, 2: 1000, 3: 3000, 4: 6000, 5: 10000, 6: 15000, 7: 25000, 8: 40000, 9: 60000, 10: 90000, 11: 150000}
            points_for_next = levels.get(next_level, 150000)
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
            print(f"API call error: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': f'API error: {str(e)}'}
