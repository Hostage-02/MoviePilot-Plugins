import base64
import json
import time
from typing import List, Tuple, Dict, Any, Optional

import requests
from app.core.config import settings
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.string import StringUtils


class ProwlarrIndexer(_PluginBase):
    # 插件元数据
    plugin_name = "Prowlarr索引器"
    plugin_desc = "使用Prowlarr扩展内建索引器支持的站点"
    plugin_icon = "Ward_A.png"
    plugin_version = "1.0"
    plugin_author = "alex007"
    plugin_config_prefix = "prowlarrindexer_"
    plugin_order = 30
    auth_level = 2

    # 私有属性
    siteshelper = None
    _enabled = False
    _prowlarr_url = ""
    _prowlarr_api_key = ""
    _indexers = {}
    _auto_update = False
    _update_interval = 12
    _last_update_time = 0
    _proxy = False

    def init_plugin(self, config: dict = None):
        self.siteshelper = SitesHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._prowlarr_url = config.get("prowlarr_url", "").strip("/")
            self._prowlarr_api_key = config.get("prowlarr_api_key", "")
            self._auto_update = config.get("auto_update", False)
            self._update_interval = config.get("update_interval", 12)
            self._proxy = config.get("proxy", False)
            
            if self._enabled and self._prowlarr_url and self._prowlarr_api_key:
                # 初始化时更新索引器
                self.update_indexers()

    def get_state(self) -> bool:
        return self._enabled

    def update_indexers(self):
        """
        从Prowlarr获取索引器配置并添加到MoviePilot
        """
        if not self._enabled or not self._prowlarr_url or not self._prowlarr_api_key:
            return
        
        # 检查是否需要更新
        current_time = time.time()
        if self._last_update_time and current_time - self._last_update_time < self._update_interval * 3600:
            return
        
        try:
            # 获取Prowlarr索引器列表
            url = f"{self._prowlarr_url}/api/v1/indexer"
            headers = {"X-Api-Key": self._prowlarr_api_key}
            
            response = requests.get(url, headers=headers, timeout=30, 
                                   proxies=settings.PROXY if self._proxy else None)
            
            if response.status_code != 200:
                logger.error(f"获取Prowlarr索引器失败: {response.status_code}")
                return
            
            indexers = response.json()
            if not indexers:
                logger.info("未从Prowlarr获取到索引器")
                return
            
            # 清空旧的索引器缓存
            self._indexers = {}
            
            # 处理每个索引器
            for indexer in indexers:
                if not indexer.get("enable", False):
                    continue
                
                indexer_id = indexer.get("id")
                indexer_name = indexer.get("name")
                
                # 获取索引器详细配置
                indexer_config = self._get_indexer_config(indexer)
                if not indexer_config:
                    continue
                
                # 缓存索引器配置
                self._indexers[indexer_id] = indexer_config
                
                # 获取索引器域名
                domain = self._get_indexer_domain(indexer)
                if not domain:
                    continue
                
                # 添加到MoviePilot索引器
                self.siteshelper.add_indexer(domain, indexer_config)
                logger.info(f"成功添加Prowlarr索引器: {indexer_name} ({domain})")
            
            # 更新最后更新时间
            self._last_update_time = current_time
            
        except Exception as e:
            logger.error(f"更新Prowlarr索引器异常: {str(e)}")

    def _get_indexer_domain(self, indexer: dict) -> Optional[str]:
        """
        获取索引器域名
        """
        try:
            # 尝试从配置中获取域名
            if indexer.get("fields"):
                for field in indexer.get("fields", []):
                    if field.get("name") == "baseUrl" and field.get("value"):
                        return StringUtils.get_url_domain(field.get("value"))
            
            # 尝试从定义中获取域名
            if indexer.get("definitionName"):
                return indexer.get("definitionName").lower()
            
            return None
        except Exception as e:
            logger.error(f"获取索引器域名异常: {str(e)}")
            return None

    def _get_indexer_config(self, indexer: dict) -> Optional[dict]:
        """
        转换Prowlarr索引器配置为MoviePilot格式
        """
        try:
            indexer_id = indexer.get("id")
            indexer_name = indexer.get("name")
            
            # 获取索引器基本URL
            base_url = None
            for field in indexer.get("fields", []):
                if field.get("name") == "baseUrl" and field.get("value"):
                    base_url = field.get("value").strip("/")
                    break
            
            if not base_url:
                logger.warning(f"索引器 {indexer_name} 没有baseUrl")
                return None
            
            # 构建MoviePilot索引器配置
            config = {
                "id": f"prowlarr_{indexer_id}",
                "name": indexer_name,
                "domain": base_url,
                "encoding": "UTF-8",
                "public": not indexer.get("privacy", "private") == "private",
                "proxy": self._proxy,
                "result_num": 100,
                "timeout": 30,
            }
            
            # 添加搜索配置
            config["search"] = {
                "paths": [{
                    "path": f"{self._prowlarr_url}/api/v1/search",
                    "method": "get"
                }],
                "params": {
                    "apikey": self._prowlarr_api_key,
                    "indexerIds": indexer_id,
                    "query": "{keyword}"
                }
            }
            
            # 添加结果解析配置
            config["torrents"] = {
                "list": {
                    "selector": "json",
                    "result_path": "$..[]"
                },
                "fields": {
                    "title": {
                        "selector": "title"
                    },
                    "description": {
                        "selector": "description"
                    },
                    "download": {
                        "selector": "downloadUrl"
                    },
                    "size": {
                        "selector": "size"
                    },
                    "seeders": {
                        "selector": "seeders"
                    },
                    "leechers": {
                        "selector": "leechers"
                    },
                    "grabs": {
                        "selector": "grabs"
                    },
                    "date_added": {
                        "selector": "publishDate"
                    },
                    "imdbid": {
                        "selector": "imdbId"
                    }
                }
            }
            
            return config
        except Exception as e:
            logger.error(f"转换索引器配置异常: {str(e)}")
            return None

    def test_connection(self):
        """
        测试Prowlarr连接
        """
        if not self._prowlarr_url or not self._prowlarr_api_key:
            self.post_message(
                title="测试失败",
                text="Prowlarr地址或API密钥未配置"
            )
            return False
        
        try:
            url = f"{self._prowlarr_url}/api/v1/system/status"
            headers = {"X-Api-Key": self._prowlarr_api_key}
            response = requests.get(url, headers=headers, timeout=10,
                                   proxies=settings.PROXY if self._proxy else None)
            
            if response.status_code == 200:
                self.post_message(
                    title="测试成功",
                    text="Prowlarr连接正常"
                )
                return True
            else:
                self.post_message(
                    title="测试失败",
                    text=f"Prowlarr返回状态码: {response.status_code}"
                )
                return False
        except Exception as e:
            self.post_message(
                title="测试失败",
                text=f"连接异常: {str(e)}"
            )
            return False

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        """
        return [{
            "path": "/update_indexers",
            "endpoint": self.api_update_indexers,
            "methods": ["GET"],
            "summary": "更新索引器",
            "description": "从Prowlarr更新索引器配置",
        }]

    def api_update_indexers(self):
        """
        API更新索引器
        """
        self.update_indexers()
        return {"success": True, "message": "索引器更新完成"}

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        """
        if self._enabled and self._auto_update:
            return [{
                "id": "ProwlarrIndexerUpdate",
                "name": "Prowlarr索引器更新服务",
                "trigger": "interval",
                "func": self.update_indexers,
                "kwargs": {
                    "hours": self._update_interval
                }
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
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
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'prowlarr_url',
                                            'label': 'Prowlarr地址',
                                            'placeholder': 'http://127.0.0.1:9696'
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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'prowlarr_api_key',
                                            'label': 'API密钥',
                                            'type': 'password'
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
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'auto_update',
                                            'label': '自动更新',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'update_interval',
                                            'label': '更新间隔(小时)',
                                            'type': 'number'
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
                                    'cols': 12
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
                                            'text': '插件会从Prowlarr获取索引器配置并添加到MoviePilot的内建索引器中。启用插件后会自动更新一次，之后可以手动更新或设置定时更新。'
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
            "auto_update": False,
            "update_interval": 12,
            "proxy": False
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面
        """
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'text': '当前已添加的Prowlarr索引器:'
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
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'headers': [
                                        {
                                            'title': 'ID',
                                            'key': 'id'
                                        },
                                        {
                                            'title': '名称',
                                            'key': 'name'
                                        },
                                        {
                                            'title': '域名',
                                            'key': 'domain'
                                        },
                                        {
                                            'title': '公开',
                                            'key': 'public'
                                        }
                                    ],
                                    'items': self._indexers.values() if self._indexers else []
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
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VBtn',
                                'props': {
                                    'color': 'primary',
                                    'text': '立即更新索引器',
                                    'variant': 'elevated',
                                    'onClick': 'update_indexers'
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        退出插件
        """
        pass