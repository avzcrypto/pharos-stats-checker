#!/usr/bin/env python3
"""
Vercel API для Pharos Stats Checker
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

class PharosStatsAPI:
    def __init__(self):
        self.api_base = "https://api.pharosnetwork.xyz"
        # Фиксированный Bearer токен
        self.bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Authorization': f'Bearer {self.bearer_token}',
            'Origin': 'https://testnet.pharosnetwork.xyz',
            'Referer': 'https://testnet.pharosnetwork.xyz/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    def validate_address(self, address):
        if not address or len(address) != 42 or not address.startswith('0x'):
            return False
        try:
            int(address[2:], 16)
            return True
        except:
            return False

    def get_profile_data(self, address):
        try:
            response = requests.get(
                f"{self.api_base}/user/profile",
                params={'address': address},
                headers=self.headers,
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    return data.get('data', {}).get('user_info', {})
            return None
        except:
            return None

    def get_tasks_data(self, address):
        try:
            response = requests.get(
                f"{self.api_base}/user/tasks",
                params={'address': address},
                headers=self.headers,
                timeout=15
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    user_tasks = data.get('data', {}).get('user_tasks', [])
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
            return None
        except:
            return None

    def calculate_level(self, points):
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

    def calculate_points_for_level(self, level):
        levels = {1: 0, 2: 1000, 3: 3000, 4: 6000, 5: 10000, 6: 15000, 7: 25000, 8: 40000, 9: 60000, 10: 90000,
                  11: 150000}
        return levels.get(level, 150000)

def check_wallet_stats_api(wallet_address):
    """API функция для веб-интерфейса"""
    try:
        api = PharosStatsAPI()

        if not api.validate_address(wallet_address):
            return {"error": "Invalid wallet address format"}

        profile_data = api.get_profile_data(wallet_address)
        tasks_data = api.get_tasks_data(wallet_address)

        if profile_data and tasks_data:
            total_points = profile_data.get('TotalPoints', 0)
            current_level = api.calculate_level(total_points)
            next_level = current_level + 1
            points_for_next = api.calculate_points_for_level(next_level)
            points_needed = points_for_next - total_points

            return {
                "success": True,
                "address": wallet_address.lower(),
                "total_points": total_points,
                "current_level": current_level,
                "next_level": next_level,
                "points_needed": points_needed,
                "send_count": tasks_data['send_count'],
                "swap_count": tasks_data['swap_count'],
                "lp_count": tasks_data['lp_count'],
                "social_tasks": tasks_data['social_tasks']
            }
        else:
            return {"error": "Unable to fetch wallet data"}

    except Exception as e:
        return {"error": str(e)}

@app.route('/api/check-wallet', methods=['POST'])
def check_wallet():
    """API endpoint для проверки статистики кошелька"""
    try:
        data = request.get_json()

        if not data or 'wallet_address' not in data:
            return jsonify({
                'success': False,
                'error': 'wallet_address is required'
            }), 400

        wallet_address = data['wallet_address']
        result = check_wallet_stats_api(wallet_address)

        if 'error' in result:
            return jsonify({
                'success': False,
                'error': result['error']
            }), 400

        return jsonify(result)

    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

@app.route('/api/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'message': 'Pharos Stats API is running'})

# Vercel handler
def handler(request, response):
    return app(request, response)
