"""
Pharos Stats Checker API 
=======================================================

Author: @avzcrypto
License: MIT
Version: 3.1.0 
"""

from http.server import BaseHTTPRequestHandler
import json
import requests
import os
import threading
import hashlib
from datetime import datetime, timedelta
import time
import random
import concurrent.futures
from typing import Optional, Dict, Any, List


class UnifiedCacheManager:
    """Production-grade unified Redis caching with 1-hour TTL and race condition protection."""
    
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.redis_enabled = redis_client is not None
        self.cache_ttl = 3600  # 1 hour for everything
        self._locks = {}  # For race condition protection
        
        # Cache key patterns
        self.user_prefix = "pharos:user:"
        self.leaderboard_key = "pharos:leaderboard:hourly"
        self.lock_prefix = "pharos:lock:"
    
    def get_user_stats(self, wallet: str) -> Optional[Dict[str, Any]]:
        """Get complete user statistics from cache with validation."""
        if not self.redis_enabled:
            return None
            
        try:
            cache_key = f"{self.user_prefix}{wallet.lower()}"
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    # Validate cache integrity
                    if self._validate_user_cache(data):
                        return data
                    else:
                        # Remove corrupted cache asynchronously
                        threading.Thread(
                            target=self._safe_delete, 
                            args=(cache_key,),
                            daemon=True
                        ).start()
                except json.JSONDecodeError:
                    # Remove corrupted cache
                    self._safe_delete(cache_key)
            
            return None
        except Exception as e:
            print(f"Cache get error for {wallet}: {e}")
            return None
    
    def set_user_stats(self, wallet: str, data: Dict[str, Any]) -> None:
        """Store complete user statistics in cache for 1 hour with lock protection."""
        if not self.redis_enabled:
            return
            
        cache_key = f"{self.user_prefix}{wallet.lower()}"
        lock_key = f"{self.lock_prefix}{wallet.lower()}"
        
        try:
            # Use Redis lock to prevent race conditions
            lock_acquired = self.redis_client.set(
                lock_key, "locked", nx=True, ex=30  # 30 second lock
            )
            
            if lock_acquired:
                try:
                    # Add cache metadata
                    cache_data = {
                        **data,
                        'cached_at': datetime.now().isoformat(),
                        'cache_version': '3.1'
                    }
                    
                    serialized = json.dumps(cache_data, separators=(',', ':'))
                    self.redis_client.setex(cache_key, self.cache_ttl, serialized)
                    
                finally:
                    # Always release lock
                    self.redis_client.delete(lock_key)
            
        except Exception as e:
            print(f"Cache set error for {wallet}: {e}")
            # Try to release lock on error
            try:
                self.redis_client.delete(lock_key)
            except:
                pass
    
    def get_total_users_count(self) -> int:
        """Get total users count from leaderboard cache (no duplication)."""
        if not self.redis_enabled:
            return 270000
            
        try:
            # Primary: From leaderboard cache (single source of truth)
            leaderboard_data = self.redis_client.get(self.leaderboard_key)
            if leaderboard_data:
                try:
                    data = json.loads(leaderboard_data)
                    if data.get('success') and isinstance(data.get('total_users'), int):
                        return data['total_users']
                except (json.JSONDecodeError, KeyError):
                    pass
            
            # Fallback: Direct query if leaderboard cache is empty
            count = self.redis_client.zcard('pharos:leaderboard')
            return count if count > 0 else 270000
            
        except Exception as e:
            print(f"Total users count error: {e}")
            return 270000
    
    def _validate_user_cache(self, data: Dict[str, Any]) -> bool:
        """Validate cached user data integrity."""
        required_fields = ['success', 'address', 'total_points']
        return (
            all(field in data for field in required_fields) and
            isinstance(data.get('success'), bool) and
            isinstance(data.get('total_points'), int) and
            len(data.get('address', '')) == 42
        )
    
    def _safe_delete(self, key: str) -> None:
        """Safely delete a Redis key with error handling."""
        try:
            self.redis_client.delete(key)
        except Exception:
            pass  # Ignore deletion errors
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics using SCAN for production safety."""
        stats = {
            'cache_enabled': self.redis_enabled,
            'cache_ttl': f"{self.cache_ttl}s (1 hour)",
            'cache_version': '3.1_production'
        }
        
        if self.redis_enabled:
            try:
                # Use SCAN instead of KEYS for production safety
                cached_users = 0
                cursor = 0
                while True:
                    cursor, keys = self.redis_client.scan(
                        cursor=cursor, 
                        match=f"{self.user_prefix}*", 
                        count=100
                    )
                    cached_users += len(keys)
                    if cursor == 0:
                        break
                
                # Check system caches
                lb_exists = self.redis_client.exists(self.leaderboard_key)
                
                stats.update({
                    'cached_users': cached_users,
                    'leaderboard_cached': bool(lb_exists),
                    'estimated_hit_rate': f"{min(95, cached_users * 0.1):.1f}%"
                })
                
            except Exception as e:
                stats['redis_error'] = str(e)
        
        return stats
    
    def clear_expired_cache(self) -> Dict[str, int]:
        """Clear corrupted entries using SCAN for production safety."""
        cleared = {'users': 0, 'system': 0}
        
        if not self.redis_enabled:
            return cleared
        
        try:
            # Use SCAN to find user cache keys
            cursor = 0
            while True:
                cursor, keys = self.redis_client.scan(
                    cursor=cursor,
                    match=f"{self.user_prefix}*",
                    count=50  # Process in small batches
                )
                
                for key in keys:
                    try:
                        data = self.redis_client.get(key)
                        if data:
                            parsed = json.loads(data)
                            if not self._validate_user_cache(parsed):
                                self.redis_client.delete(key)
                                cleared['users'] += 1
                    except:
                        # Remove corrupted entries
                        self.redis_client.delete(key)
                        cleared['users'] += 1
                
                if cursor == 0:
                    break
            
        except Exception as e:
            print(f"Cache cleanup error: {e}")
        
        return cleared


class ProxyManager:
    """Production-grade proxy manager with validation."""
    
    def __init__(self):
        self.proxies = self._load_proxies()
        self._validate_proxies()
    
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
                        
                        # Basic validation
                        if self._validate_proxy_format(host, port, username, password):
                            proxy_url = f"http://{username}:{password}@{host}:{port}"
                            proxies.append(proxy_url)
            
            return proxies
        except Exception as e:
            print(f"Proxy loading error: {e}")
            return []
    
    def _validate_proxy_format(self, host: str, port: str, username: str, password: str) -> bool:
        """Validate proxy configuration format."""
        try:
            # Basic validation
            if not host or not port or not username:
                return False
            
            # Port should be numeric
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                return False
            
            # Host should not be empty and contain valid characters
            if not host.replace('.', '').replace('-', '').isalnum():
                return False
            
            return True
        except:
            return False
    
    def _validate_proxies(self) -> None:
        """Log proxy validation results."""
        valid_count = len(self.proxies)
        if valid_count > 0:
            print(f"✅ Loaded {valid_count} valid proxies")
        else:
            print("⚠️ No valid proxies loaded - using direct connections")
    
    def get_random_proxy(self) -> Optional[str]:
        """Get a random proxy from the validated pool."""
        return random.choice(self.proxies) if self.proxies else None


class RedisManager:
    """Production-grade Redis manager with comprehensive error handling."""
    
    def __init__(self):
        self.client = None
        self.enabled = self._initialize_connection()
    
    def _initialize_connection(self) -> bool:
        """Initialize Redis connection with comprehensive error handling."""
        try:
            import redis
            redis_url = os.environ.get('REDIS_URL', '')
            if not redis_url:
                print("⚠️ REDIS_URL not found in environment")
                return False
                
            self.client = redis.Redis.from_url(
                redis_url,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=20  # Connection pooling
            )
            
            # Test connection
            self.client.ping()
            print("✅ Redis connection established")
            return True
            
        except ImportError:
            print("❌ Redis library not installed")
            return False
        except Exception as e:
            print(f"❌ Redis connection failed: {e}")
            return False
    
    def get_exact_rank(self, total_points: int) -> Optional[int]:
        """Calculate exact user rank with error handling."""
        try:
            if not self.enabled or not self.client:
                return None
            
            users_with_more_points = self.client.zcount(
                'pharos:leaderboard', 
                total_points + 1, 
                '+inf'
            )
            return users_with_more_points + 1
            
        except Exception as e:
            print(f"Rank calculation error: {e}")
            return None
    
    def save_user_stats(self, user_data: Dict[str, Any]) -> None:
        """Save user statistics with comprehensive error handling."""
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
                'faroswap_swaps': user_data.get('faroswap_swaps', 0),
                'exact_rank': user_data.get('exact_rank'),
                'rank_calculated_at': timestamp,
                'total_users_count': user_data.get('total_users_count', 270000),
                # NEW TASKS
                'primuslabs_send': user_data.get('primuslabs_send', 0),
                'aquaflux': user_data.get('aquaflux', 0),
                'autostaking': user_data.get('autostaking', 0),
                'fiamma_bridge': user_data.get('fiamma_bridge', 0),
                'brokex': user_data.get('brokex', 0)
            }
            
            # Get existing data
            existing_data = self.client.hget('pharos:users', address)
            if existing_data:
                try:
                    existing_stats = json.loads(existing_data)
                    stats['total_checks'] = existing_stats.get('total_checks', 0) + 1
                    stats['first_check'] = existing_stats.get('first_check', timestamp)
                    if existing_stats.get('member_since'):
                        stats['member_since'] = existing_stats.get('member_since')
                except json.JSONDecodeError:
                    pass  # Use new data if existing is corrupted
            else:
                stats['first_check'] = timestamp
            
            # Batch Redis operations with timeout
            pipe = self.client.pipeline()
            pipe.hset('pharos:users', address, json.dumps(stats, separators=(',', ':')))
            pipe.zadd('pharos:leaderboard', {address: user_data['total_points']})
            pipe.incr('pharos:total_checks')
            
            # Execute with timeout
            pipe.execute()
            
        except Exception as e:
            print(f"Save user stats error for {user_data.get('address', 'unknown')}: {e}")
    
    def get_leaderboard_data(self) -> Dict[str, Any]:
        """Get leaderboard data with 1-hour cache and comprehensive error handling."""
        if not self.enabled:
            return {'success': False, 'error': 'Redis not available'}
        
        try:
            # Check hourly cache
            cache_key = 'pharos:leaderboard:hourly'
            cached_data = self.client.get(cache_key)
            
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    if data.get('success'):
                        data['cached'] = True
                        data['cache_info'] = 'Updated hourly - next refresh within 1 hour'
                        return data
                except json.JSONDecodeError:
                    # Corrupted cache, will recalculate
                    print("⚠️ Corrupted leaderboard cache detected, recalculating...")
            
            # Calculate fresh data
            print("🔄 Calculating fresh leaderboard data...")
            fresh_data = self._calculate_full_leaderboard()
            
            if fresh_data.get('success'):
                # Cache for 1 hour
                cache_ttl = 3600
                try:
                    self.client.setex(
                        cache_key, 
                        cache_ttl, 
                        json.dumps(fresh_data, separators=(',', ':'))
                    )
                except Exception as e:
                    print(f"Failed to cache leaderboard: {e}")
                
                fresh_data['cached'] = False
                fresh_data['cache_info'] = 'Freshly calculated - cached for 1 hour'
            
            return fresh_data
            
        except Exception as e:
            print(f"Error in get_leaderboard_data: {e}")
            return {'success': False, 'error': f'Leaderboard calculation failed: {str(e)}'}

    def _calculate_full_leaderboard(self) -> Dict[str, Any]:
        """Full leaderboard calculation with production-grade error handling."""
        try:
            # Get ALL wallets and their points with timeout
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
                    'point_distribution': {
                        '14000+': 0, '13000-13999': 0, '12000-12999': 0, '11000-11999': 0,
                        '10000-10999': 0, '9000-9999': 0, '8000-8999': 0, '7000-7999': 0,
                        '6000-6999': 0, 'below-6000': 0
                    },
                    'last_updated': datetime.now().isoformat()
                }
            
            # Generate top-100 for display with error handling
            leaderboard = []
            for i, (wallet_bytes, points) in enumerate(all_wallets[:100], 1):
                try:
                    wallet = wallet_bytes.decode('utf-8') if isinstance(wallet_bytes, bytes) else str(wallet_bytes)
                    
                    # Get detailed user statistics
                    user_data = self.client.hget('pharos:users', wallet)
                    stats = {}
                    if user_data:
                        try:
                            stats = json.loads(user_data)
                        except json.JSONDecodeError:
                            pass  # Use empty stats if corrupted
                    
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
                        'faroswap_swaps': stats.get('faroswap_swaps', 0),
                        # NEW TASKS
                        'primuslabs_send': stats.get('primuslabs_send', 0),
                        'aquaflux': stats.get('aquaflux', 0),
                        'autostaking': stats.get('autostaking', 0),
                        'fiamma_bridge': stats.get('fiamma_bridge', 0),
                        'brokex': stats.get('brokex', 0)
                    })
                except Exception as e:
                    print(f"Error processing wallet {i}: {e}")
                    continue
            
            # Calculate point distribution for ALL users - UPDATED TIERS
            point_distribution = {
                '14000+': 0,
                '13000-13999': 0, 
                '12000-12999': 0,
                '11000-11999': 0,
                '10000-10999': 0,
                '9000-9999': 0,
                '8000-8999': 0,
                '7000-7999': 0,
                '6000-6999': 0,
                'below-6000': 0
            }
            
            for wallet_bytes, points in all_wallets:
                try:
                    points = int(points)
                    if points >= 14000:
                        point_distribution['14000+'] += 1
                    elif points >= 13000:
                        point_distribution['13000-13999'] += 1
                    elif points >= 12000:
                        point_distribution['12000-12999'] += 1
                    elif points >= 11000:
                        point_distribution['11000-11999'] += 1
                    elif points >= 10000:
                        point_distribution['10000-10999'] += 1
                    elif points >= 9000:
                        point_distribution['9000-9999'] += 1
                    elif points >= 8000:
                        point_distribution['8000-8999'] += 1
                    elif points >= 7000:
                        point_distribution['7000-7999'] += 1
                    elif points >= 6000:
                        point_distribution['6000-6999'] += 1
                    else:
                        point_distribution['below-6000'] += 1
                except (ValueError, TypeError):
                    continue
            
            total_users = len(all_wallets)
            
            # Get total checks safely
            try:
                total_checks = self.client.get('pharos:total_checks')
                total_checks = int(total_checks) if total_checks else 0
            except (ValueError, TypeError):
                total_checks = 0
            
            return {
                'success': True,
                'total_users': total_users,
                'total_checks': total_checks,
                'leaderboard': leaderboard,
                'point_distribution': point_distribution,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"Critical error calculating leaderboard: {e}")
            return {
                'success': False, 
                'error': f'Leaderboard calculation failed: {str(e)}'
            }

    def clear_leaderboard_cache(self) -> bool:
        """Clear hourly leaderboard cache for forced refresh."""
        if not self.enabled:
            return False
        
        try:
            cache_key = 'pharos:leaderboard:hourly'
            
            # Clear leaderboard cache
            cleared_count = self.client.delete(cache_key)
            
            print(f"✅ Cleared {cleared_count} cache key successfully")
            return True
            
        except Exception as e:
            print(f"❌ Error clearing cache: {e}")
            return False


class PharosAPIClient:
    """Production-grade Pharos API client with comprehensive error handling."""
    
    API_BASE = "https://api.pharosnetwork.xyz"
    BEARER_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODA5MTQ3NjEsImlhdCI6MTc0OTM3ODc2MSwic3ViIjoiMHgyNkIxMzVBQjFkNjg3Mjk2N0I1YjJjNTcwOWNhMkI1RERiREUxMDZGIn0.k1JtNw2w67q7lw1kFHmSXxapUS4GpBwXdZH3ByVMFfg"
    
    def __init__(self, proxy_manager: ProxyManager, redis_manager: RedisManager):
        self.proxy_manager = proxy_manager
        self.redis_manager = redis_manager
        # Cache manager will be set after initialization to avoid circular dependency
        self.cache_manager = None
        
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Authorization': f'Bearer {self.BEARER_TOKEN}',
            'Origin': 'https://testnet.pharosnetwork.xyz',
            'Referer': 'https://testnet.pharosnetwork.xyz/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def set_cache_manager(self, cache_manager: UnifiedCacheManager) -> None:
        """Set cache manager after initialization to avoid circular dependency."""
        self.cache_manager = cache_manager
    
    def get_user_data(self, wallet_address: str) -> Dict[str, Any]:
        """Get user data with unified caching and race condition protection."""
        # Try cache first if available
        if self.cache_manager:
            cached_result = self.cache_manager.get_user_stats(wallet_address)
            if cached_result:
                return cached_result
        
        # Cache miss - fetch from API with retry logic
        for attempt in range(2):
            try:
                # Configure request parameters based on attempt
                if attempt == 0:
                    proxy_url = self.proxy_manager.get_random_proxy()
                    proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
                    timeout = 15
                else:
                    proxies = None
                    timeout = 12
                
                # Concurrent API calls with timeout protection
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
                    
                    # Wait for results with timeout
                    try:
                        profile_response = profile_future.result(timeout=timeout + 5)
                        tasks_response = tasks_future.result(timeout=timeout + 5)
                    except concurrent.futures.TimeoutError:
                        print(f"API timeout for wallet {wallet_address} on attempt {attempt + 1}")
                        continue
                
                if profile_response and tasks_response:
                    result = self._process_api_response(
                        profile_response, 
                        tasks_response, 
                        wallet_address
                    )
                    
                    # Cache successful result
                    if result.get('success') and self.cache_manager:
                        self.cache_manager.set_user_stats(wallet_address, result)
                        
                        # Also save to Redis for leaderboard
                        if self.redis_manager.enabled:
                            try:
                                self.redis_manager.save_user_stats(result)
                            except Exception as e:
                                print(f"Failed to save to Redis: {e}")
                    
                    return result
                
            except (requests.exceptions.ProxyError, 
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError) as e:
                print(f"Connection error on attempt {attempt + 1}: {e}")
                if attempt == 0:
                    continue
                else:
                    return {'success': False, 'error': 'Connection failed after retries'}
            except Exception as e:
                print(f"Unexpected error on attempt {attempt + 1}: {e}")
                if attempt == 0:
                    continue
                else:
                    return {'success': False, 'error': f'API error: {str(e)}'}
        
        return {'success': False, 'error': 'All connection attempts failed'}
    
    def _make_request(self, url: str, params: Dict[str, str], 
                     proxies: Optional[Dict[str, str]], timeout: int) -> Optional[Dict[str, Any]]:
        """Make HTTP request with comprehensive error handling."""
        try:
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=False
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get('code') == 0:
                        return data
                    else:
                        print(f"API returned error code: {data.get('code')}")
                except json.JSONDecodeError:
                    print(f"Invalid JSON response from {url}")
            else:
                print(f"HTTP {response.status_code} from {url}")
            
            return None
            
        except requests.exceptions.Timeout:
            print(f"Timeout requesting {url}")
            return None
        except requests.exceptions.ConnectionError:
            print(f"Connection error requesting {url}")
            return None
        except Exception as e:
            print(f"Unexpected error requesting {url}: {e}")
            return None
    
    def _process_api_response(self, profile_data: Dict[str, Any], 
                            tasks_data: Dict[str, Any], 
                            wallet_address: str) -> Dict[str, Any]:
        """Process and normalize API response data with validation."""
        try:
            # Validate input data structure
            if not isinstance(profile_data, dict) or not isinstance(tasks_data, dict):
                return {'success': False, 'error': 'Invalid API response format'}
            
            user_info = profile_data.get('data', {}).get('user_info', {})
            total_points = user_info.get('TotalPoints', 0)
            user_tasks = tasks_data.get('data', {}).get('user_tasks', [])
            
            # Validate points
            if not isinstance(total_points, (int, float)) or total_points < 0:
                total_points = 0
            
            # Validate tasks
            if not isinstance(user_tasks, list):
                user_tasks = []
            
            # Parse task data efficiently
            task_counts = self._parse_task_data(user_tasks)
            
           # Calculate user level based on points
            current_level = self._calculate_level(int(total_points))
            next_level = min(current_level + 1, 5)  # Cap at level 5
            
            # Calculate points needed for next level
            level_thresholds = {
                1: 0, 2: 1001, 3: 3501, 4: 6001, 5: 10001, 
                6: 20001  # Next threshold after level 5
            }
            points_for_next = level_thresholds.get(next_level, 20001)
            points_needed = max(0, points_for_next - int(total_points))
            
            # Get exact rank from Redis
            exact_rank = self.redis_manager.get_exact_rank(int(total_points))
            
            # Get total users count from cache manager
            total_users_count = 270000  # Default fallback
            if self.cache_manager:
                total_users_count = self.cache_manager.get_total_users_count()
            
            return {
                'success': True,
                'address': wallet_address.lower(),
                'total_points': int(total_points),
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
                'zenith_lp': task_counts['lp'],
                # NEW TASKS
                'primuslabs_send': task_counts['primuslabs_send'],
                'aquaflux': task_counts['aquaflux'],
                'autostaking': task_counts['autostaking'],
                'fiamma_bridge': task_counts['fiamma_bridge'],
                'brokex': task_counts['brokex'],
                'total_users_count': total_users_count
            }
            
        except Exception as e:
            return {
                'success': False, 
                'error': f'Response processing error: {str(e)}'
            }
    
    def _parse_task_data(self, user_tasks: List[Dict[str, Any]]) -> Dict[str, int]:
        """Parse task completion data with validation."""
        task_counts = {
            'send': 0, 'swap': 0, 'lp': 0, 'domain': 0, 'nft': 0,
            'faroswap_lp': 0, 'faroswap_swaps': 0, 'social': 0,
            'primuslabs_send': 0, 'aquaflux': 0, 'autostaking': 0, 
            'fiamma_bridge': 0, 'brokex': 0
        }
        
        for task in user_tasks:
            try:
                if not isinstance(task, dict):
                    continue
                
                task_id = task.get('TaskId', 0)
                complete_times = task.get('CompleteTimes', 0)
                
                # Validate task data
                if not isinstance(task_id, int) or not isinstance(complete_times, (int, float)):
                    continue
                
                complete_times = max(0, int(complete_times))  # Ensure non-negative
                
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
                # NEW TASKS
                elif task_id == 108:
                    task_counts['primuslabs_send'] = complete_times
                elif task_id == 109:
                    task_counts['fiamma_bridge'] = complete_times
                elif task_id == 110:
                    task_counts['autostaking'] = complete_times
                elif task_id == 111:
                    task_counts['brokex'] = complete_times
                elif task_id == 112:
                    task_counts['aquaflux'] = complete_times
                    
            except Exception as e:
                print(f"Error parsing task {task}: {e}")
                continue
        
        return task_counts
    
    def _calculate_level(self, total_points: int) -> int:
        """Calculate user level based on total points with validation."""
        try:
            if total_points <= 1000:
                return 1
            elif total_points <= 3500:
                return 2
            elif total_points <= 6000:
                return 3
            elif total_points <= 10000:
                return 4
            elif total_points <= 20000:
                return 5
            else:
                return 5  # Cap at level 5
        except:
            return 1  # Default to level 1 on any error


# Module-level managers with proper initialization order
proxy_manager = ProxyManager()
redis_manager = RedisManager()
cache_manager = UnifiedCacheManager(redis_manager.client if redis_manager.enabled else None)
api_client = PharosAPIClient(proxy_manager, redis_manager)

# Set cache manager after initialization to avoid circular dependency
api_client.set_cache_manager(cache_manager)


class handler(BaseHTTPRequestHandler):
    """Production-grade HTTP request handler with comprehensive error handling."""
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests with comprehensive routing."""
        try:
            if self.path == '/api/health':
                self._handle_health_check()
            elif self.path == '/api/admin/stats':
                self._handle_admin_stats()
            elif self.path == '/api/refresh-leaderboard':
                self._handle_refresh_leaderboard()
            elif self.path == '/api/cache/clear':
                self._handle_cache_clear()
            elif self.path == '/api/cache/stats':
                self._handle_cache_stats()
            else:
                self._send_error_response({'error': 'Endpoint not found'}, 404)
        except Exception as e:
            print(f"Unexpected error in GET handler: {e}")
            self._send_error_response({'error': 'Internal server error'}, 500)
    
    def do_POST(self):
        """Handle POST requests with validation."""
        try:
            if self.path == '/api/check-wallet':
                self._handle_wallet_check()
            else:
                self._send_error_response({'error': 'Endpoint not found'}, 404)
        except Exception as e:
            print(f"Unexpected error in POST handler: {e}")
            self._send_error_response({'error': 'Internal server error'}, 500)
    
    def _handle_health_check(self):
        """Return comprehensive API health status."""
        try:
            cache_stats = cache_manager.get_cache_stats()
            
            # Test Redis connectivity
            redis_status = 'disconnected'
            if redis_manager.enabled:
                try:
                    redis_manager.client.ping()
                    redis_status = 'healthy'
                except:
                    redis_status = 'error'
            
            response_data = {
                'status': 'ok',
                'message': 'Pharos Stats API is operational',
                'version': '3.1.0',
                'timestamp': datetime.now().isoformat(),
                'system_status': {
                    'redis': redis_status,
                    'proxies_loaded': len(proxy_manager.proxies),
                    'cache_enabled': cache_manager.redis_enabled
                },
                'caching_system': {
                    'type': 'unified_redis_production',
                    'ttl': '1_hour_for_all',
                    'stats': cache_stats
                },
                'auto_refresh': {
                    'enabled': True,
                    'schedule': '0 * * * * (hourly)',
                    'endpoint': '/api/refresh-leaderboard'
                }
            }
            self._send_json_response(response_data)
            
        except Exception as e:
            self._send_error_response({'error': f'Health check failed: {str(e)}'}, 500)
    
    def _handle_admin_stats(self):
        """Handle admin statistics with error handling."""
        try:
            if not redis_manager.enabled:
                self._send_error_response({
                    'error': 'Statistics service unavailable',
                    'reason': 'Redis not connected'
                }, 503)
                return
            
            stats_data = redis_manager.get_leaderboard_data()
            
            # Add system metadata
            stats_data['system_info'] = {
                'cache_enabled': cache_manager.redis_enabled,
                'total_api_calls': 'tracked_in_redis',
                'version': '3.1.0'
            }
            
            self._send_json_response(stats_data)
            
        except Exception as e:
            print(f"Error in admin stats: {e}")
            self._send_error_response({
                'error': 'Failed to fetch statistics',
                'details': str(e)
            }, 500)
    
    def _handle_refresh_leaderboard(self):
        """Handle hourly leaderboard refresh with comprehensive logging."""
        try:
            start_time = time.time()
            print(f"🔄 Hourly leaderboard refresh started at {datetime.now().isoformat()}")
            
            if not redis_manager.enabled:
                self._send_error_response({
                    'error': 'Redis not available for refresh',
                    'timestamp': datetime.now().isoformat()
                }, 503)
                return
            
            # Clear hourly caches
            cache_cleared = redis_manager.clear_leaderboard_cache()
            
            if cache_cleared:
                # Generate fresh data
                fresh_data = redis_manager.get_leaderboard_data()
                
                if fresh_data.get('success'):
                    execution_time = round(time.time() - start_time, 2)
                    
                    response = {
                        'success': True,
                        'message': 'Hourly leaderboard refresh completed successfully',
                        'timestamp': datetime.now().isoformat(),
                        'execution_time_seconds': execution_time,
                        'total_users': fresh_data.get('total_users', 0),
                        'total_checks': fresh_data.get('total_checks', 0),
                        'cache_type': 'hourly_refresh',
                        'next_refresh': 'in_1_hour'
                    }
                    
                    print(f"✅ Leaderboard refreshed: {fresh_data.get('total_users', 0)} users in {execution_time}s")
                    self._send_json_response(response)
                else:
                    self._send_error_response({
                        'error': 'Failed to generate fresh leaderboard data',
                        'details': fresh_data.get('error', 'unknown')
                    }, 500)
            else:
                self._send_error_response({
                    'error': 'Failed to clear cache for refresh',
                    'timestamp': datetime.now().isoformat()
                }, 500)
                
        except Exception as e:
            print(f"❌ Critical error in hourly refresh: {e}")
            self._send_error_response({
                'error': 'Refresh process failed',
                'details': str(e),
                'timestamp': datetime.now().isoformat()
            }, 500)
    
    def _handle_cache_clear(self):
        """Handle manual cache clearing with detailed reporting."""
        try:
            start_time = time.time()
            cleared_stats = cache_manager.clear_expired_cache()
            execution_time = round(time.time() - start_time, 2)
            
            response = {
                'success': True,
                'message': 'Cache cleanup completed successfully',
                'cleared_entries': cleared_stats,
                'execution_time_seconds': execution_time,
                'timestamp': datetime.now().isoformat()
            }
            self._send_json_response(response)
            
        except Exception as e:
            print(f"Cache clear error: {e}")
            self._send_error_response({
                'error': 'Cache cleanup failed',
                'details': str(e)
            }, 500)
    
    def _handle_cache_stats(self):
        """Handle detailed cache statistics request."""
        try:
            stats = cache_manager.get_cache_stats()
            
            # Add Redis connection details if available
            if redis_manager.enabled:
                try:
                    info = redis_manager.client.info()
                    stats['redis_info'] = {
                        'connected_clients': info.get('connected_clients', 0),
                        'used_memory_human': info.get('used_memory_human', 'unknown'),
                        'keyspace_hits': info.get('keyspace_hits', 0),
                        'keyspace_misses': info.get('keyspace_misses', 0),
                        'total_commands_processed': info.get('total_commands_processed', 0)
                    }
                    
                    # Calculate hit rate if possible
                    hits = info.get('keyspace_hits', 0)
                    misses = info.get('keyspace_misses', 0)
                    if hits + misses > 0:
                        stats['redis_hit_rate'] = f"{(hits / (hits + misses) * 100):.2f}%"
                        
                except Exception as redis_error:
                    stats['redis_info_error'] = str(redis_error)
            
            response = {
                'success': True,
                'cache_statistics': stats,
                'timestamp': datetime.now().isoformat(),
                'system_version': '3.1.0'
            }
            self._send_json_response(response)
            
        except Exception as e:
            self._send_error_response({
                'error': 'Failed to fetch cache statistics',
                'details': str(e)
            }, 500)
    
    def _handle_wallet_check(self):
        """Handle wallet statistics check with comprehensive validation."""
        try:
            # Validate request size
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 1000:
                self._send_error_response({
                    'error': 'Request payload too large',
                    'max_size': '1000 bytes'
                }, 413)
                return
            
            if content_length == 0:
                self._send_error_response({
                    'error': 'Empty request body'
                }, 400)
                return
            
            # Parse request data
            try:
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
            except json.JSONDecodeError:
                self._send_error_response({
                    'error': 'Invalid JSON format'
                }, 400)
                return
            except UnicodeDecodeError:
                self._send_error_response({
                    'error': 'Invalid character encoding'
                }, 400)
                return
            
            # Validate wallet address
            wallet_address = data.get('wallet_address', '').strip()
            
            if not wallet_address:
                self._send_error_response({
                    'error': 'Missing wallet_address field'
                }, 400)
                return
            
            if not self._is_valid_address(wallet_address):
                self._send_error_response({
                    'error': 'Invalid Ethereum address format',
                    'expected_format': '0x followed by 40 hexadecimal characters'
                }, 400)
                return
            
            # Get user data using unified caching
            result = api_client.get_user_data(wallet_address)
            
            if result.get('success'):
                self._send_json_response(result)
            else:
                self._send_error_response(result, 400)
                
        except Exception as e:
            print(f"Unexpected error in wallet check: {e}")
            self._send_error_response({
                'error': 'Internal server error during wallet check',
                'request_id': hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
            }, 500)
    
    def _is_valid_address(self, address: str) -> bool:
        """Validate Ethereum address format with comprehensive checks."""
        try:
            return (
                isinstance(address, str) and
                len(address) == 42 and 
                address.startswith('0x') and 
                all(c in '0123456789abcdefABCDEF' for c in address[2:])
            )
        except:
            return False
    
    def _send_json_response(self, data: Dict[str, Any], status_code: int = 200):
        """Send JSON response with proper headers and error handling."""
        try:
            if 'success' not in data:
                data['success'] = True
            
            response_body = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
            response_bytes = response_body.encode('utf-8')
            
            self.send_response(status_code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Content-Length', str(response_bytes))
            self.end_headers()
            
            self.wfile.write(response_bytes)
            
        except Exception as e:
            print(f"Error sending JSON response: {e}")
            # Fallback error response
            try:
                error_response = '{"success":false,"error":"Response encoding failed"}'
                error_bytes = error_response.encode('utf-8')
                
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(error_bytes)))
                self.end_headers()
                self.wfile.write(error_bytes)
            except:
                pass  # Last resort - silent failure
    
    def _send_error_response(self, error_data: Dict[str, Any], status_code: int):
        """Send error response with proper formatting."""
        error_data['success'] = False
        if 'timestamp' not in error_data:
            error_data['timestamp'] = datetime.now().isoformat()
        
        self._send_json_response(error_data, status_code)
