import json
import time
from typing import List, Dict, Any, Optional, Tuple

import requests
from app.core.config import settings
from app.core.event import eventmanager
from app.core.event.chain import ChainEventType
from app.core.event.event import Event
from app.db.models.site import Site
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TorrentInfo
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class ProwlarrIndexer(_PluginBase):
    # 插件元数据
    plugin_name = "Prowlarr索引器"
    plugin_desc = "将Prowlarr中的索引器动态注册到MoviePilot"
    plugin_icon = "Prowlarr.png"
    plugin_version = "1.0"
    plugin_author = "MoviePilot"
    plugin_config_prefix = "prowlarrindexer_"
    plugin_order = 30
    auth_level = 2

    # 私有属性
    _enabled = False
    _prowlarr_url = ""
    _prowlarr_api_key = ""
    _proxy = False
    _indexers_cache = {}  # 缓存Prowlarr索引器信息
    _cache_expires = 0  # 缓存过期时间
    _cache_ttl = 3600  # 缓存有效期，默认1小时
    _sites_helper = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        if config:
            self._enabled = config.get("enabled", False)
            self._prowlarr_url = config.get("prowlarr_url", "").strip().rstrip('/')
            self._prowlarr_api_key = config.get("prowlarr_api_key", "")
            self._proxy = config.get("proxy", False)
            
        self._sites_helper = SitesHelper()
        
        # 初始化时刷新一次缓存并注册索引器
        if self.get_state():
            self._refresh_indexers_cache()
            self._register_indexers()

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled and self._prowlarr_url and self._prowlarr_api_key

    def _refresh_indexers_cache(self):
        """
        刷新Prowlarr索引器缓存
        """
        try:
            # 获取Prowlarr索引器列表
            indexers = self._get_prowlarr_indexers()
            if not indexers:
                logger.error("获取Prowlarr索引器失败")
                return
                
            # 清空缓存
            self._indexers_cache = {}
            
            # 更新缓存
            for indexer in indexers:
                indexer_id = indexer.get("id")
                if not indexer_id:
                    continue
                    
                # 获取索引器名称和域名
                name = indexer.get("name")
                
                # 获取索引器URL
                base_url = None
                for field in indexer.get("fields", []):
                    if field.get("name") == "baseUrl" and field.get("value"):
                        base_url = field.get("value").strip("/")
                        break
                        
                if not base_url:
                    continue
                    
                # 获取站点域名
                domain = StringUtils.get_url_domain(base_url)
                
                # 缓存索引器信息
                self._indexers_cache[indexer_id] = {
                    "name": name,
                    "url": base_url,
                    "domain": domain
                }
                
            # 更新缓存过期时间
            self._cache_expires = time.time() + self._cache_ttl
            logger.info(f"Prowlarr索引器缓存刷新完成，共 {len(self._indexers_cache)} 个索引器")
            
        except Exception as e:
            logger.error(f"刷新Prowlarr索引器缓存出错: {str(e)}")

    def _get_prowlarr_indexers(self) -> List[Dict]:
        """
        获取Prowlarr索引器列表
        """
        if not self._prowlarr_url or not self._prowlarr_api_key:
            return []
            
        url = f"{self._prowlarr_url}/api/v1/indexer"
        headers = {
            "X-Api-Key": self._prowlarr_api_key
        }
        
        try:
            response = RequestUtils(headers=headers, proxies=settings.PROXY if self._proxy else None).get_res(url)
            if response and response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"获取Prowlarr索引器列表出错: {str(e)}")
            
        return []

    def _register_indexers(self):
        """
        将Prowlarr索引器注册到MoviePilot
        """
        if not self._indexers_cache:
            return
            
        registered_count = 0
        for indexer_id, indexer_info in self._indexers_cache.items():
            try:
                # 构建索引器配置
                indexer_config = {
                    "id": f"prowlarr_{indexer_id}",
                    "name": f"[Prowlarr] {indexer_info['name']}",
                    "domain": indexer_info["domain"],
                    "url": indexer_info["url"],
                    "proxy": self._proxy,
                    "parser": "Prowlarr"
                }
                
                # 注册索引器
                self._sites_helper.add_indexer(indexer_info["domain"], indexer_config)
                registered_count += 1
                
            except Exception as e:
                logger.error(f"注册索引器 {indexer_info['name']} 失败: {str(e)}")
                
        logger.info(f"成功注册 {registered_count}/{len(self._indexers_cache)} 个Prowlarr索引器到MoviePilot")

    @eventmanager.register(ChainEventType.SearchTorrent)
    def handle_search(self, event: Event):
        """
        处理搜索请求
        """
        if not self.get_state():
            return
            
        # 获取事件数据
        event_data = event.event_data
        if not event_data:
            return
            
        # 获取搜索参数
        keyword = event_data.keyword
        sites = event_data.sites
        if not keyword or not sites:
            return
            
        # 检查缓存是否过期
        if time.time() > self._cache_expires:
            self._refresh_indexers_cache()
            
        # 找出本插件注册的索引器
        prowlarr_sites = []
        for site in sites:
            if f"prowlarr_{site.id}" in [f"prowlarr_{i}" for i in self._indexers_cache.keys()]:
                prowlarr_sites.append(site)
                
        if not prowlarr_sites:
            return
            
        # 调用Prowlarr搜索
        results = self._search_prowlarr(keyword, prowlarr_sites)
        if results:
            # 合并结果
            if event_data.torrents:
                event_data.torrents.extend(results)
            else:
                event_data.torrents = results
                
        # 更新站点列表，移除已处理的站点
        event_data.sites = [site for site in sites if site not in prowlarr_sites]

    def _search_prowlarr(self, keyword: str, sites: List[Site]) -> List[TorrentInfo]:
        """
        调用Prowlarr API进行搜索
        """
        if not keyword or not sites:
            return []
            
        # 获取索引器ID
        indexer_ids = []
        for site in sites:
            if site.id in self._indexers_cache:
                indexer_ids.append(str(self._indexers_cache[site.id]["id"]))
                
        if not indexer_ids:
            return []
            
        # 构建请求
        url = f"{self._prowlarr_url}/api/v1/search"
        params = {
            "query": keyword,
            "indexerIds": ",".join(indexer_ids),
            "limit": 100
        }
        headers = {
            "X-Api-Key": self._prowlarr_api_key
        }
        
        try:
            response = RequestUtils(
                headers=headers,
                proxies=settings.PROXY if self._proxy else None
            ).get_res(url, params=params)
            
            if not response or response.status_code != 200:
                logger.error(f"Prowlarr搜索失败: {response.status_code if response else '无响应'}")
                return []
                
            return self._convert_results(response.json(), sites)
            
        except Exception as e:
            logger.error(f"Prowlarr搜索出错: {str(e)}")
            return []

    def _convert_results(self, results: List[Dict], sites: List[Site]) -> List[TorrentInfo]:
        """
        转换Prowlarr返回结果为MoviePilot格式
        """
        torrents = []
        site_map = {site.id: site for site in sites}
        
        for result in results:
            try:
                # 获取对应的站点
                indexer_id = result.get("indexerId")
                site_id = None
                for sid, info in self._indexers_cache.items():
                    if str(info["id"]) == str(indexer_id):
                        site_id = sid
                        break
                        
                if not site_id or site_id not in site_map:
                    continue
                    
                site = site_map[site_id]
                
                # 构建种子信息
                torrent = TorrentInfo()
                torrent.site = site.name
                torrent.site_order = site.pri
                torrent.site_cookie = site.cookie
                torrent.site_ua = site.ua
                torrent.site_proxy = site.proxy
                torrent.site_id = site.id
                
                # 基本信息
                torrent.title = result.get("title", "")
                torrent.description = result.get("description", "")
                torrent.enclosure = result.get("downloadUrl", "")
                torrent.page_url = result.get("infoUrl", "")
                torrent.size = result.get("size", 0)
                torrent.seeders = result.get("seeders", 0)
                torrent.peers = result.get("leechers", 0)
                torrent.grabs = result.get("grabs", 0)
                
                # 发布时间
                if result.get("publishDate"):
                    torrent.pubdate = result.get("publishDate")
                    
                # IMDB ID
                if result.get("imdbId"):
                    torrent.imdbid = result.get("imdbId")
                    
                # 分类
                categories = result.get("categories", [])
                if categories:
                    torrent.category = ",".join([str(c) for c in categories])
                    
                torrents.append(torrent)
                
            except Exception as e:
                logger.error(f"转换搜索结果出错: {str(e)}")
                
        return torrents

    @eventmanager.register(EventType.PluginReload)
    def handle_reload(self, event: Event):
        """
        处理插件重载事件
        """
        if not self.get_state():
            return
            
        logger.info("收到插件重载事件，刷新Prowlarr索引器缓存")
        self._refresh_indexers_cache()
        self._register_indexers()

    def get_form(self) -> Tuple[List[dict], dict]:
        """
        获取插件配置表单
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'proxy',
                                            'label': '使用代理',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'prowlarr_url',
                                            'label': 'Prowlarr地址',
                                            'placeholder': 'http://localhost:9696'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'prowlarr_api_key',
                                            'label': 'API密钥',
                                            'placeholder': 'Prowlarr API密钥'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VBtn',
                                        'props': {
                                            'color': 'primary',
                                            'text': '测试连接',
                                            'variant': 'elevated',
                                            'onClick': 'test_connection'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '插件会将Prowlarr中的索引器动态注册到MoviePilot，需要确保Prowlarr中已配置相应的索引器。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "prowlarr_url": "",
            "prowlarr_api_key": "",
            "proxy": False
        }

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        """
        return [{
            "path": "/test_connection",
            "endpoint": self.api_test_connection,
            "methods": ["GET"],
            "summary": "测试Prowlarr连接",
            "description": "测试与Prowlarr的连接是否正常",
        }, {
            "path": "/refresh_cache",
            "endpoint": self.api_refresh_cache,
            "methods": ["GET"],
            "summary": "刷新索引器缓存",
            "description": "手动刷新Prowlarr索引器缓存并重新注册",
        }]

    def api_test_connection(self):
        """
        测试Prowlarr连接
        """
        if not self._prowlarr_url or not self._prowlarr_api_key:
            return {"code": 1, "msg": "请先配置Prowlarr地址和API密钥"}
            
        url = f"{self._prowlarr_url}/api/v1/system/status"
        headers = {
            "X-Api-Key": self._prowlarr_api_key
        }
        
        try:
            response = RequestUtils(headers=headers, proxies=settings.PROXY if self._proxy else None).get_res(url)
            if response and response.status_code == 200:
                version = response.json()[0].get("version", "未知")
                return {"code": 0, "msg": f"连接成功，Prowlarr版本: {version}"}
            else:
                return {"code": 1, "msg": f"连接失败: {response.status_code if response else '未知错误'}"}
        except Exception as e:
            return {"code": 1, "msg": f"连接异常: {str(e)}"}

    def api_refresh_cache(self):
        """
        刷新索引器缓存并重新注册
        """
        if not self.get_state():
            return {"code": 1, "msg": "插件未启用或配置不完整"}
            
        try:
            self._refresh_indexers_cache()
            self._register_indexers()
            return {"code": 0, "msg": f"缓存刷新并注册完成，共 {len(self._indexers_cache)} 个索引器"}
        except Exception as e:
            return {"code": 1, "msg": f"刷新缓存失败: {str(e)}"}

    def test_connection(self):
        """
        测试连接
        """
        return self.api_test_connection()