"""
Pharos Stats Checker API
=====================================

Author: @avzcrypto
License: MIT
Version: 2.2.0
"""

from http.server import BaseHTTPRequestHandler
import json
import requests
import os
from datetime import datetime
import time
import random
import concurrent.futures
from typing import Optional, Dict, Any, List


class CacheManager:
    """Advanced in-memory cache with LRU eviction policy."""
    
    def __init__(self, ttl: int = 300, max_size: int = 2000):
        self.cache = {}
        self.ttl = ttl
        self.max_size = max_size
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached data if still valid."""
        cache_key = key.lower()
        if cache_key in self.cache:
            data, timestamp = self.cache[cache_key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[cache_key]
        return None
    
    def set(self, key: str, data: Dict[str, Any]) -> None:
        """Store data in cache with automatic cleanup."""
        cache_key = key.lower()
        self.cache[cache_key] = (data, time.time())
        self._cleanup_if_needed()
    
    def _cleanup_if_needed(self) -> None:
        """Cleanup oldest entries when cache exceeds maximum size."""
        if len(self.cache) > self.max_size:
            sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
            remove_count = self.max_size // 4
            for key, _ in sorted_items[:remove_count]:
                del self.cache[key]


class ProxyManager:
    """Manages proxy rotation for external API calls."""
    
    def __init__(self):
        self.proxies = self._load_proxies()
    
    def _load_proxies(self) -> List[str]:
        """Load and parse proxy configuration from environment."""
        try:
            proxy_data = os.environ.get('PROXY_LIST', '')
            if not proxy_data:
                return []
            
            proxies = []
            for line in proxy_data.replace('\\n', '\n').split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split(':')
                    if len(parts) >= 4:
                        host, port, username = parts[0], parts[1], parts[2]
                        password = ':'.join(parts[3:])
                        proxy_url = f"http://{username}:{password}@{host}:{port}"
                        proxies.append(proxy_url)
            
            return proxies
        except Exception:
            return []
    
    def get_random_proxy(self) -> Optional[str]:
        """Get a random proxy from the pool."""
        return random.choice(self.proxies) if self.proxies else None


class RedisManager:
    """Redis connection and operations manager with 24h leaderboard cache."""
    
    def __init__(self):
        self.client = None
        self.enabled = self._initialize_connection()
    
    def _initialize_connection(self) -> bool:
        """Initialize Redis connection with error handling."""
        try:
            import redis
            redis_url = os.environ.get('REDIS_URL', '')
            if not redis_url:
                return False
                
            self.client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            self.client.ping()
            return True
        except Exception:
            return False
    
    def get_exact_rank(self, total_points: int) -> Optional[int]:
        """Calculate exact user rank based on points."""
        try:
            if not self.enabled or not self.client:
                return None
            
            users_with_more_points = self.client.zcount(
                'pharos:leaderboard', 
                total_points + 1, 
                '+inf'
            )
            return users_with_more_points + 1
        except Exception:
            return None
    
    def save_user_stats(self, user_data: Dict[str, Any]) -> None:
        """Save user statistics to Redis with batched operations."""
        if not self.enabled or not self.client:
            return
        
        try:
            address = user_data['address'].lower()
            timestamp = datetime.now().isoformat()
            
            stats = {
                'address': address,
                'total_points': user_data['total_points'],
                'current_level': user_data['current_level'],
                'send_count': user_data['send_count'],
                'swap_count': user_data['swap_count'],
                'lp_count': user_data['lp_count'],
                'social_tasks': user_data['social_tasks'],
                'member_since': user_data.get('member_since'),
                'last_check': timestamp,
                'total_checks': 1,
                'mint_domain': user_data.get('mint_domain', 0),
                'mint_nft': user_data.get('mint_nft', 0),
                'faroswap_lp': user_data.get('faroswap_lp', 0),
                'faroswap_swaps': user_data.get('faroswap_swaps', 0)
            }
            
            existing_data = self.client.hget('pharos:users', address)
            if existing_data:
                existing_stats = json.loads(existing_data)
                stats['total_checks'] = existing_stats.get('total_checks', 0) + 1
                stats['first_check'] = existing_stats.get('first_check', timestamp)
                if existing_stats.get('member_since'):
                    stats['member_since'] = existing_stats.get('member_since')
            else:
                stats['first_check'] = timestamp
            
            # Batch Redis operations for efficiency
            pipe = self.client.pipeline()
            pipe.hset('pharos:users', address, json.dumps(stats))
            pipe.zadd('pharos:leaderboard', {address: user_data['total_points']})
            pipe.incr('pharos:total_checks')
            pipe.execute()
            
        except Exception:
            pass  # Graceful degradation if Redis fails
    
    def get_leaderboard_data(self) -> Dict[str, Any]:
        """Получение данных лидерборда с кэшем 24 часа."""
        if not self.enabled:
            return {'success': False, 'error': 'Statistics not available'}
        
        try:
            # Проверяем кэш
            cache_key = 'pharos:leaderboard:daily'
            cached_data = self.client.get(cache_key)
            
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    # Добавляем информацию о кэше
                    data['cached'] = True
                    data['cache_info'] = 'Updated daily at 00:00 UTC via auto-refresh'
                    return data
                except json.JSONDecodeError:
                    # Если кэш поврежден, пересчитываем
                    pass
            
            # Кэш пустой или поврежден - делаем полный расчет
            print("Calculating fresh leaderboard data...")
            fresh_data = self._calculate_full_leaderboard()
            
            # Кэшируем на 24 часа (86400 секунд)
            cache_ttl = 86400
            self.client.setex(cache_key, cache_ttl, json.dumps(fresh_data))
            
            # Добавляем информацию о свежих данных
            fresh_data['cached'] = False
            fresh_data['cache_info'] = 'Freshly calculated - next update in 24h'
            
            return fresh_data
            
        except Exception as e:
            print(f"Error in get_leaderboard_data: {e}")
            return {'success': False, 'error': str(e)}

    def _calculate_full_leaderboard(self) -> Dict[str, Any]:
        """Полный расчет лидерборда (тяжелая операция)."""
        try:
            # Получаем ВСЕ кошельки и их очки
            all_wallets = self.client.zrevrange(
                'pharos:leaderboard', 0, -1, 
                withscores=True
            )
            
            if not all_wallets:
                return {
                    'success': True,
                    'total_users': 0,
                    'total_checks': 0,
                    'leaderboard': [],
                    'point_distribution': {},
                    'last_updated': datetime.now().isoformat()
                }
            
            # Формируем топ-100 для отображения
            leaderboard = []
            for i, (wallet_bytes, points) in enumerate(all_wallets[:100], 1):
                wallet = wallet_bytes.decode('utf-8') if isinstance(wallet_bytes, bytes) else str(wallet_bytes)
                
                # Получаем детальную статистику пользователя
                user_data = self.client.hget('pharos:users', wallet)
                stats = {}
                if user_data:
                    try:
                        stats = json.loads(user_data)
                    except json.JSONDecodeError:
                        pass
                
                leaderboard.append({
                    'rank': i,
                    'address': wallet,
                    'total_points': int(points),
                    'current_level': stats.get('current_level', 1),
                    'send_count': stats.get('send_count', 0),
                    'swap_count': stats.get('swap_count', 0),
                    'lp_count': stats.get('lp_count', 0),
                    'social_tasks': stats.get('social_tasks', 0),
                    'member_since': stats.get('member_since'),
                    'last_check': stats.get('last_check'),
                    'total_checks': stats.get('total_checks', 1),
                    'first_check': stats.get('first_check'),
                    'mint_domain': stats.get('mint_domain', 0),
                    'mint_nft': stats.get('mint_nft', 0),
                    'faroswap_lp': stats.get('faroswap_lp', 0),
                    'faroswap_swaps': stats.get('faroswap_swaps', 0)
                })
            
            # Рассчитываем распределение по тирам для ВСЕХ пользователей
            point_distribution = {
                '10000+': 0,
                '9000-9999': 0,
                '8000-8999': 0,
                '7000-7999': 0,
                '6000-6999': 0,
                '5000-5999': 0,
                '4000-4999': 0,
                '3000-3999': 0,
                'below-3000': 0
            }
            
            for wallet_bytes, points in all_wallets:
                points = int(points)
                if points >= 10000:
                    point_distribution['10000+'] += 1
                elif points >= 9000:
                    point_distribution['9000-9999'] += 1
                elif points >= 8000:
                    point_distribution['8000-8999'] += 1
                elif points >= 7000:
                    point_distribution['7000-7999'] += 1
                elif points >= 6000:
                    point_distribution['6000-6999'] += 1
                elif points >= 5000:
                    point_distribution['5000-5999'] += 1
                elif points >= 4000:
                    point_distribution['4000-4999'] += 1
                elif points >= 3000:
                    point_distribution['3000-3999'] += 1
                else:
                    point_distribution['below-3000'] += 1
            
            total_users = len(all_wallets)
            total_checks = self.client.get('pharos:total_checks')
            
            return {
                'success': True,
                'total_users': total_users,
                'total_checks': int(total_checks) if total_checks else 0,
                'leaderboard': leaderboard,
                'point_distribution': point_distribution,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"Error calculating leaderboard: {e}")
            return {'success': False, 'error': str(e)}

    def clear_leaderboard_cache(self) -> bool:
        """Очистить кэш лидерборда (для принудительного обновления)."""
        if not self.enabled:
            return False
        
        try:
            cache_key = 'pharos:leaderboard:daily'
            self.client.delete(cache_key)
            print("Leaderboard cache cleared successfully")
            return True
        except Exception as e:
            print(f"Error clearing cache: {e}")
            return False


class PharosAPIClient:
    """High-performance client for Pharos Network API with concurrent processing."""
    
    API_BASE = "https://api.pharosnetwork.xyz"
    BEARER_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"
    
    def __init__(self, proxy_manager: ProxyManager, redis_manager: RedisManager):
        self.proxy_manager = proxy_manager
        self.redis_manager = redis_manager
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Authorization': f'Bearer {self.BEARER_TOKEN}',
            'Origin': 'https://testnet.pharosnetwork.xyz',
            'Referer': 'https://testnet.pharosnetwork.xyz/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def get_user_data(self, wallet_address: str) -> Dict[str, Any]:
        """Fetch user data with optimized concurrent API calls and fallback logic."""
        for attempt in range(2):
            try:
                if attempt == 0:
                    proxy_url = self.proxy_manager.get_random_proxy()
                    proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
                    timeout = 15
                else:
                    proxies = None
                    timeout = 12
                
                # Concurrent API calls for improved performance
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    profile_future = executor.submit(
                        self._make_request,
                        f"{self.API_BASE}/user/profile",
                        {'address': wallet_address},
                        proxies,
                        timeout
                    )
                    
                    tasks_future = executor.submit(
                        self._make_request,
                        f"{self.API_BASE}/user/tasks",
                        {'address': wallet_address},
                        proxies,
                        timeout
                    )
                    
                    profile_response = profile_future.result()
                    tasks_response = tasks_future.result()
                
                if profile_response and tasks_response:
                    return self._process_api_response(
                        profile_response, 
                        tasks_response, 
                        wallet_address
                    )
                
            except (requests.exceptions.ProxyError, 
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError):
                if attempt == 0:
                    continue
                else:
                    return {'success': False, 'error': 'Connection failed'}
            except Exception as e:
                if attempt == 0:
                    continue
                else:
                    return {'success': False, 'error': f'API error: {str(e)}'}
        
        return {'success': False, 'error': 'All connection attempts failed'}
    
    def _make_request(self, url: str, params: Dict[str, str], 
                     proxies: Optional[Dict[str, str]], timeout: int) -> Optional[Dict[str, Any]]:
        """Make HTTP request with error handling."""
        try:
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                proxies=proxies,
                timeout=timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    return data
            
            return None
        except Exception:
            return None
    
    def _process_api_response(self, profile_data: Dict[str, Any], 
                            tasks_data: Dict[str, Any], 
                            wallet_address: str) -> Dict[str, Any]:
        """Process and normalize API response data."""
        try:
            user_info = profile_data.get('data', {}).get('user_info', {})
            total_points = user_info.get('TotalPoints', 0)
            user_tasks = tasks_data.get('data', {}).get('user_tasks', [])
            
            # Parse task data efficiently
            task_counts = self._parse_task_data(user_tasks)
            
            # Calculate user level based on points
            current_level = self._calculate_level(total_points)
            next_level = current_level + 1
            
            # Calculate points needed for next level
            level_thresholds = {
                1: 0, 2: 1000, 3: 3000, 4: 6000, 5: 10000, 
                6: 15000, 7: 25000, 8: 40000, 9: 60000, 
                10: 90000, 11: 150000
            }
            points_for_next = level_thresholds.get(next_level, 150000)
            points_needed = max(0, points_for_next - total_points)
            
            # Get exact rank from Redis
            exact_rank = self.redis_manager.get_exact_rank(total_points)
            
            return {
                'success': True,
                'address': wallet_address.lower(),
                'total_points': total_points,
                'exact_rank': exact_rank,
                'current_level': current_level,
                'next_level': next_level,
                'points_needed': points_needed,
                'send_count': task_counts['send'],
                'swap_count': task_counts['swap'],
                'lp_count': task_counts['lp'],
                'social_tasks': task_counts['social'],
                'member_since': user_info.get('CreateTime'),
                'mint_domain': task_counts['domain'],
                'mint_nft': task_counts['nft'],
                'faroswap_lp': task_counts['faroswap_lp'],
                'faroswap_swaps': task_counts['faroswap_swaps'],
                'zenith_swaps': task_counts['swap'],
                'zenith_lp': task_counts['lp']
            }
            
        except Exception as e:
            return {'success': False, 'error': f'Response processing error: {str(e)}'}
    
    def _parse_task_data(self, user_tasks: List[Dict[str, Any]]) -> Dict[str, int]:
        """Parse task completion data from API response."""
        task_counts = {
            'send': 0, 'swap': 0, 'lp': 0, 'domain': 0, 'nft': 0,
            'faroswap_lp': 0, 'faroswap_swaps': 0, 'social': 0
        }
        
        for task in user_tasks:
            task_id = task.get('TaskId', 0)
            complete_times = task.get('CompleteTimes', 0)
            
            if task_id == 103:
                task_counts['send'] = complete_times
            elif task_id == 101:
                task_counts['swap'] = complete_times
            elif task_id == 102:
                task_counts['lp'] = complete_times
            elif task_id in [201, 202, 203, 204]:
                task_counts['social'] += 1
            elif task_id == 104:
                task_counts['domain'] = complete_times
            elif task_id == 105:
                task_counts['nft'] = complete_times
            elif task_id == 106:
                task_counts['faroswap_lp'] = complete_times
            elif task_id == 107:
                task_counts['faroswap_swaps'] = complete_times
        
        return task_counts
    
    def _calculate_level(self, total_points: int) -> int:
        """Calculate user level based on total points."""
        if total_points < 1000:
            return 1
        elif total_points < 3000:
            return 2
        elif total_points < 6000:
            return 3
        elif total_points < 10000:
            return 4
        elif total_points < 15000:
            return 5
        elif total_points < 25000:
            return 6
        elif total_points < 40000:
            return 7
        elif total_points < 60000:
            return 8
        elif total_points < 90000:
            return 9
        else:
            return 10


# Module-level managers (Vercel serverless compatible)
cache_manager = CacheManager(ttl=300, max_size=2000)
proxy_manager = ProxyManager()
redis_manager = RedisManager()
api_client = PharosAPIClient(proxy_manager, redis_manager)


class handler(BaseHTTPRequestHandler):
    """Main HTTP request handler optimized for Vercel serverless deployment."""
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests for health check and admin statistics."""
        if self.path == '/api/health':
            self._handle_health_check()
        elif self.path == '/api/admin/stats':
            self._handle_admin_stats()
        elif self.path == '/api/refresh-leaderboard':
            self._handle_refresh_leaderboard()
        else:
            self.send_error(404)
    
    def do_POST(self):
        """Handle POST requests for wallet statistics."""
        if self.path == '/api/check-wallet':
            self._handle_wallet_check()
        else:
            self.send_error(404)
    
    def _handle_health_check(self):
        """Return API health status and configuration."""
        response_data = {
            'status': 'ok',
            'message': 'Pharos Stats API is operational',
            'version': '2.2.0',
            'cache_size': len(cache_manager.cache),
            'proxies_loaded': len(proxy_manager.proxies),
            'redis_enabled': redis_manager.enabled,
            'auto_refresh': {
                'enabled': True,
                'schedule': '0 0 * * * (daily at 00:00 UTC)',
                'endpoint': '/api/refresh-leaderboard'
            }
        }
        self._send_json_response(response_data)
    
    def _handle_admin_stats(self):
        """Handle admin statistics request with 24h cache."""
        try:
            if not redis_manager.enabled:
                self._send_error_response({'error': 'Statistics not available'}, 503)
                return
            
            # Используем новый метод с 24h кэшем
            stats_data = redis_manager.get_leaderboard_data()
            self._send_json_response(stats_data)
            
        except Exception as e:
            print(f"Error in admin stats: {e}")
            self._send_error_response({'error': 'Failed to fetch statistics'}, 500)
    
    def _handle_refresh_leaderboard(self):
        """Обработчик принудительного обновления лидерборда (для cron)."""
        try:
            print(f"🔄 Leaderboard refresh requested at {datetime.now().isoformat()}")
            
            if not redis_manager.enabled:
                self._send_error_response({'error': 'Redis not available'}, 503)
                return
            
            # Очищаем кэш
            cache_cleared = redis_manager.clear_leaderboard_cache()
            
            if cache_cleared:
                # Генерируем свежие данные
                fresh_data = redis_manager.get_leaderboard_data()
                
                if fresh_data.get('success'):
                    response = {
                        'success': True,
                        'message': 'Leaderboard refreshed successfully',
                        'timestamp': datetime.now().isoformat(),
                        'total_users': fresh_data.get('total_users', 0),
                        'total_checks': fresh_data.get('total_checks', 0)
                    }
                    print(f"✅ Leaderboard refreshed: {fresh_data.get('total_users', 0)} users")
                    self._send_json_response(response)
                else:
                    self._send_error_response({'error': 'Failed to generate fresh data'}, 500)
            else:
                self._send_error_response({'error': 'Failed to clear cache'}, 500)
                
        except Exception as e:
            print(f"❌ Error in refresh handler: {e}")
            self._send_error_response({'error': f'Refresh failed: {str(e)}'}, 500)
    
    def _handle_wallet_check(self):
        """Handle wallet statistics check request."""
        try:
            # Parse and validate request
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 1000:
                self._send_error_response({'error': 'Request too large'}, 413)
                return
            
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            wallet_address = data.get('wallet_address', '').strip()
            
            # Validate wallet address format
            if not self._is_valid_address(wallet_address):
                self._send_error_response({'error': 'Invalid wallet address format'}, 400)
                return
            
            # Check cache first (primary optimization)
            cached_result = cache_manager.get(wallet_address)
            if cached_result:
                self._send_json_response(cached_result)
                return
            
            # Fetch fresh data from API
            result = api_client.get_user_data(wallet_address)
            
            if result.get('success'):
                # Cache successful result
                cache_manager.set(wallet_address, result)
                
                # Save to Redis asynchronously (non-blocking)
                if redis_manager.enabled:
                    try:
                        redis_manager.save_user_stats(result)
                    except Exception:
                        pass  # Graceful degradation
                
                self._send_json_response(result)
            else:
                self._send_error_response(result, 400)
                
        except json.JSONDecodeError:
            self._send_error_response({'error': 'Invalid JSON format'}, 400)
        except Exception as e:
            print(f"Error in wallet check: {e}")
            self._send_error_response({'error': 'Internal server error'}, 500)
    
    def _is_valid_address(self, address: str) -> bool:
        """Validate Ethereum address format."""
        return (len(address) == 42 and 
                address.startswith('0x') and 
                all(c in '0123456789abcdefABCDEF' for c in address[2:]))
    
    def _send_json_response(self, data: Dict[str, Any], status_code: int = 200):
        """Send JSON response with proper headers."""
        if 'success' not in data:
            data['success'] = True
        
        response_body = json.dumps(data, separators=(',', ':'))
        
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(response_body)))
        self.end_headers()
        
        self.wfile.write(response_body.encode('utf-8'))
    
    def _send_error_response(self, error_data: Dict[str, Any], status_code: int):
        """Send error response with proper formatting."""
        error_data['success'] = False
        self._send_json_response(error_data, status_code)
