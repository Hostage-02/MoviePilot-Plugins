import json
import requests
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path

from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, ChainEventType, MediaType
from app.core.event import eventmanager, Event
from app.schemas import NotificationType
from app.schemas.context import ResourceSelectionEventData, ResourceDownloadEventData


class ProwlarrIndexer(_PluginBase):
    # 插件元数据
    plugin_name = "Prowlarr代理"
    plugin_desc = "使用Prowlarr作为搜索和下载代理"
    plugin_icon = "Prowlarr.png"
    plugin_version = "1.0.2"
    plugin_author = "alex007"
    plugin_config_prefix = "prowlarrindexer_"
    plugin_order = 30
    auth_level = 2

    # 私有属性
    _enabled = False
    _prowlarr_url = ""
    _prowlarr_api_key = ""

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._prowlarr_url = config.get("prowlarr_url", "").strip("/")
            self._prowlarr_api_key = config.get("prowlarr_api_key", "")
            
            # 注册事件监听器
            if self._enabled and self._prowlarr_url and self._prowlarr_api_key:
                self.register_events()

    def register_events(self):
        """注册事件监听器"""
        @eventmanager.register(ChainEventType.ResourceSelection)
        def on_resource_selection(event: Event):
            """处理资源选择事件"""
            if not self._enabled or not self._prowlarr_url or not self._prowlarr_api_key:
                return
            
            event_data: ResourceSelectionEventData = event.event_data
            if not event_data or not event_data.contexts:
                return
                
            logger.debug(f"收到资源选择事件，上下文数量: {len(event_data.contexts)}")
            
            # 构造Prowlarr搜索请求
            search_results = []
            for context in event_data.contexts:
                try:
                    url = f"{self._prowlarr_url}/api/v1/search"
                    params = {
                        "query": context.title,
                        "type": "movie" if context.media_type == MediaType.MOVIE else "tv",
                        "limit": 50
                    }
                    headers = {"X-Api-Key": self._prowlarr_api_key}
                    
                    logger.debug(f"向Prowlarr发送搜索请求: {url} {params}")
                    logger.debug(f"请求头: {headers}")
                    response = requests.get(url, params=params, headers=headers, timeout=30)
                    
                    if response.status_code == 200:
                        results = response.json()
                        logger.debug(f"从Prowlarr获取到 {len(results)} 条搜索结果")
                        logger.debug(f"示例搜索结果: {json.dumps(results[:1], indent=2) if results else '无结果'}")
                        search_results.extend(results)
                    else:
                        logger.error(f"Prowlarr搜索失败，状态码: {response.status_code}, 响应: {response.text}")
                except Exception as e:
                    logger.error(f"Prowlarr搜索异常: {str(e)}")
            
            # 转换结果格式为MoviePilot所需格式
            if search_results:
                event_data.updated = True
                event_data.updated_contexts = [
                    self._convert_prowlarr_result(result)
                    for result in search_results
                    if self._convert_prowlarr_result(result)
                ]
            
        @eventmanager.register(ChainEventType.ResourceDownload)
        def on_resource_download(event: Event):
            """处理资源下载事件"""
            if not self._enabled or not self._prowlarr_url or not self._prowlarr_api_key:
                return
                
            event_data: ResourceDownloadEventData = event.event_data
            if not event_data or not event_data.context:
                return
                
            logger.debug(f"收到资源下载事件，资源: {event_data.context.title}")
            
            # 获取下载链接
            try:
                download_url = f"{self._prowlarr_url}/api/v1/indexer/{event_data.context.site}/download"
                params = {"guid": event_data.context.enclosure}
                headers = {"X-Api-Key": self._prowlarr_api_key}
                
                logger.debug(f"向Prowlarr发送下载请求: {download_url} {params}")
                logger.debug(f"请求头: {headers}")
                response = requests.get(download_url, params=params, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    # 返回种子文件内容
                    event_data.content = response.content
                    event_data.content_type = "application/x-bittorrent"
                    logger.info(f"成功从Prowlarr下载资源: {event_data.context.title}")
                    logger.debug(f"下载响应头: {response.headers}")
                else:
                    logger.error(f"Prowlarr下载失败，状态码: {response.status_code}, 响应: {response.text}")
                    self.post_message(
                        mtype=NotificationType.Error,
                        title="下载失败",
                        message=f"无法从Prowlarr下载资源: {event_data.context.title}"
                    )
            except Exception as e:
                logger.error(f"Prowlarr下载异常: {str(e)}")
                self.post_message(
                    mtype=NotificationType.Error,
                    title="下载失败",
                    message=f"下载资源时发生异常: {str(e)}"
                )

    def _convert_prowlarr_result(self, prowlarr_result: Dict) -> Optional[Dict]:
        """将Prowlarr搜索结果转换为MoviePilot格式"""
        try:
            return {
                "title": prowlarr_result.get("title"),
                "description": prowlarr_result.get("description"),
                "enclosure": prowlarr_result.get("guid"),
                "size": prowlarr_result.get("size"),
                "seeders": prowlarr_result.get("seeders", 0),
                "peers": prowlarr_result.get("peers", 0),
                "site": prowlarr_result.get("indexer"),
                "link": prowlarr_result.get("infoUrl", ""),
                "pubdate": prowlarr_result.get("publishDate", ""),
            }
        except Exception as e:
            logger.error(f"转换Prowlarr搜索结果失败: {str(e)}")
            return None

    def test_connection(self):
        """测试Prowlarr连接"""
        if not self._prowlarr_url or not self._prowlarr_api_key:
            self.post_message(
                mtype=NotificationType.Error,
                title="测试失败",
                message="请先配置Prowlarr地址和API密钥"
            )
            return
            
        try:
            url = f"{self._prowlarr_url}/api/v1/system/status"
            headers = {"X-Api-Key": self._prowlarr_api_key}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                self.post_message(
                    mtype=NotificationType.Success,
                    title="测试成功",
                    message="Prowlarr连接正常"
                )
                logger.info("Prowlarr连接测试成功")
            else:
                self.post_message(
                    mtype=NotificationType.Error,
                    title="测试失败",
                    message=f"Prowlarr返回状态码: {response.status_code}"
                )
                logger.error(f"Prowlarr连接测试失败，状态码: {response.status_code}")
        except Exception as e:
            self.post_message(
                mtype=NotificationType.Error,
                title="测试失败",
                message=f"连接异常: {str(e)}"
            )
            logger.error(f"Prowlarr连接测试异常: {str(e)}")

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
                                            'label': '启用插件'
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
                                        'component': 'VBtn',
                                        'props': {
                                            'color': 'primary',
                                            'text': '测试连接',
                                            'variant': 'elevated',
                                            'events': [{
                                                'event': 'click',
                                                'method': 'test_connection'
                                            }]
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
                    }
                ]
            }
        ], {
            "enabled": False,
            "prowlarr_url": "",
            "prowlarr_api_key": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """退出插件"""
        pass