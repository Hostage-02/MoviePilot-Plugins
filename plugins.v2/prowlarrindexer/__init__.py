import json
import base64
import requests
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path

from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.core.event import eventmanager, Event
from app.schemas import NotificationType


class ProwlarrIndexer(_PluginBase):
    # 插件元数据
    plugin_name = "Prowlarr索引器"
    plugin_desc = "从Prowlarr获取索引站点配置并添加到MoviePilot"
    plugin_icon = "Prowlarr.png"
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
    _sync_interval = 24  # 默认24小时同步一次

    def init_plugin(self, config: dict = None):
        self.siteshelper = SitesHelper()
        if config:
            self._enabled = config.get("enabled")
            self._prowlarr_url = config.get("prowlarr_url", "").strip("/")
            self._prowlarr_api_key = config.get("prowlarr_api_key", "")
            self._sync_interval = int(config.get("sync_interval", 24))
            
            if self._enabled and self._prowlarr_url and self._prowlarr_api_key:
                self.sync_prowlarr_indexers()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        配置界面
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
                                'props': {'cols': 12},
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
                                'props': {'cols': 12},
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
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'sync_interval',
                                            'label': '同步频率(小时)',
                                            'placeholder': '24'
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
            "sync_interval": 24
        }

    def get_page(self) -> List[dict]:
        pass

    def fetch_prowlarr_indexers(self) -> Optional[List[Dict]]:
        """
        从Prowlarr获取索引器列表
        """
        if not self._prowlarr_url or not self._prowlarr_api_key:
            return None
            
        try:
            url = f"{self._prowlarr_url}/api/v1/indexer"
            headers = {"X-Api-Key": self._prowlarr_api_key}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"获取Prowlarr索引器失败，状态码：{response.status_code}")
                return None
        except Exception as e:
            logger.error(f"获取Prowlarr索引器异常：{str(e)}")
            return None

    def convert_prowlarr_to_moviepilot_indexer(self, prowlarr_indexer: Dict) -> Dict:
        """
        将Prowlarr索引器转换为MoviePilot格式
        """
        # 这里需要根据实际需求进行字段映射
        return {
            "id": prowlarr_indexer.get("name", "").lower().replace(" ", "_"),
            "name": prowlarr_indexer.get("name", ""),
            "domain": next((url for url in prowlarr_indexer.get("indexerUrls", []) if url), ""),
            "public": not prowlarr_indexer.get("privacy", "private") == "private",
            "search": {
                "paths": [{"path": "/api", "method": "get"}]
            }
        }

    def sync_prowlarr_indexers(self):
        """
        同步Prowlarr索引器到MoviePilot
        """
        indexers = self.fetch_prowlarr_indexers()
        if not indexers:
            logger.error("未获取到Prowlarr索引器列表")
            return
            
        success_count = 0
        for indexer in indexers:
            try:
                if not indexer.get("enable"):
                    continue
                    
                # 转换格式
                moviepilot_indexer = self.convert_prowlarr_to_moviepilot_indexer(indexer)
                if not moviepilot_indexer.get("domain"):
                    continue
                    
                # 添加到MoviePilot
                domain = moviepilot_indexer["domain"].split("/")[2]  # 提取域名部分
                json_str = json.dumps(moviepilot_indexer, ensure_ascii=False)
                b64_str = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
                self.siteshelper.add_indexer(domain, json.loads(json_str))
                success_count += 1
            except Exception as e:
                logger.error(f"添加索引器 {indexer.get('name')} 失败：{str(e)}")
                
        logger.info(f"成功同步 {success_count}/{len(indexers)} 个索引器")

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册定时服务
        """
        if not self._enabled or not self._prowlarr_url or not self._prowlarr_api_key:
            return []
            
        return [{
            "id": "ProwlarrSync",
            "name": "Prowlarr索引器同步",
            "trigger": "interval",
            "hours": self._sync_interval,
            "func": self.sync_prowlarr_indexers,
            "kwargs": {}
        }]

    def stop_service(self):
        """
        退出插件
        """
        pass