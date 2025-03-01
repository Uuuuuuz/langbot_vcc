import os
import base64
import time
from pathlib import Path
from typing import List, Union
from pkg.plugin.context import register, handler, BasePlugin, APIHost, EventContext
from pkg.plugin.events import GroupNormalMessageReceived, PersonNormalMessageReceived, NormalMessageResponded
from mirai import Voice, Plain
import pkg.platform.types as platform_types
from graiax import silkcoder
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService, SpeechSynthesizer
import logging
import sys
import shutil

# 设置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建控制台处理器
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    '[%(asctime)s.%(msecs)03d] %(levelname)s: %(message)s',
    datefmt='%m-%d %H:%M:%S'
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 创建文件处理器
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
file_handler = logging.FileHandler(
    os.path.join(log_dir, 'voice_clone.log'),
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 命令常量
CMD_CLONE = "克隆声音："
CMD_CLONE_SPACE = "克隆声音 "
CMD_SPEAK = "用声音说："
CMD_HELP = "声音帮助"
CMD_TEST = "声音测试"
CMD_SHORT_CLONE = "克隆 "  # 新增简短克隆命令

HELP_TEXT = """
克隆声音：<语音URL> - 克隆一个新的声音
克隆声音 <语音URL> - 克隆一个新的声音（空格版本）
克隆 <文本内容> - 使用已克隆的声音说话
用声音说：<文本内容> - 使用克隆的声音说话
声音测试 - 使用预设语音进行完整测试
声音帮助 - 显示此帮助信息
"""

# 测试用的语音URL和文本
TEST_VOICE_URL = "https://uuz-1314375353.cos.ap-beijing.myqcloud.com/music/keqing.wav"
TEST_TEXT = "你好，这是一条测试消息。"

@register(name="VoiceCloneChat", description="语音克隆聊天插件", version="1.0", author="AI")
class VoiceCloneChat(BasePlugin):
    """语音克隆聊天插件类"""
    
    def __init__(self, plugin_host: APIHost):
        super().__init__(plugin_host)
        self.api_key = "yourapi"  # 建议改为从配置文件读取
        dashscope.api_key = self.api_key
        self.voice_ids = {}  # 使用字典存储每个用户的voice_id
        self.target_model = "cosyvoice-v1"
        self.logger = logger
        self.ap = plugin_host
        # 加载保存的用户声音设置
        self._load_user_settings()
        logger.info("VoiceCloneChat 插件初始化完成")

    def _load_user_settings(self):
        """加载用户声音设置"""
        settings_file = os.path.join(os.path.dirname(__file__), 'user_settings.txt')
        if os.path.exists(settings_file):
            try:
                with open(settings_file, 'r') as f:
                    for line in f:
                        parts = line.strip().split(':')
                        if len(parts) == 2:
                            user_id, voice_id = parts
                            self.voice_ids[int(user_id)] = voice_id
                self.logger.info(f"已加载用户设置，共 {len(self.voice_ids)} 条记录")
            except Exception as e:
                self.logger.error(f"加载用户设置失败: {str(e)}")
                
    def _save_user_settings(self):
        """保存用户声音设置"""
        settings_file = os.path.join(os.path.dirname(__file__), 'user_settings.txt')
        try:
            with open(settings_file, 'w') as f:
                for user_id, voice_id in self.voice_ids.items():
                    f.write(f"{user_id}:{voice_id}\n")
            self.logger.info(f"已保存用户设置，共 {len(self.voice_ids)} 条记录")
        except Exception as e:
            self.logger.error(f"保存用户设置失败: {str(e)}")

    @handler(PersonNormalMessageReceived)
    async def person_normal_message_received(self, ctx: EventContext):
        """处理私聊消息"""
        receive_text = ctx.event.text_message
        sender_id = ctx.event.sender_id
        
        self.logger.info(f"收到私聊消息: {receive_text}, sender_id={sender_id}")
        
        # 去除消息前后的空格
        receive_text = receive_text.strip()
        
        # 处理测试命令
        if receive_text == CMD_TEST:
            self.logger.info("执行测试命令，先返回克隆成功消息")
            clone_result = await self._clone_voice(sender_id, TEST_VOICE_URL)
            if "成功" not in clone_result:
                self.logger.error(f"克隆失败: {clone_result}")
                await ctx.reply([Plain(clone_result)])
                ctx.prevent_default()
                return
                
            # 返回克隆成功消息
            self.logger.info(f"返回克隆成功消息: {clone_result}")
            # 先发送文本消息
            await ctx.reply([Plain(clone_result)])
            
            # 生成测试语音
            self.logger.info("开始生成测试语音消息")
            voice_result = await self._speak_text(sender_id, TEST_TEXT)
            
            if isinstance(voice_result, str):
                # 错误情况
                self.logger.error(f"测试语音生成失败: {voice_result}")
                await ctx.reply([Plain(voice_result)])
            else:
                # 成功情况 - 直接使用Voice对象
                self.logger.info("测试语音消息生成成功，准备发送")
                # 单独发送语音消息
                await ctx.reply([voice_result])
                self.logger.info("语音消息已发送")
            
            ctx.prevent_default()
            return
            
        # 处理帮助命令
        if receive_text == CMD_HELP:
            await ctx.reply([Plain(HELP_TEXT)])
            ctx.prevent_default()
            return
            
        # 处理克隆命令（冒号版本）
        if receive_text.startswith(CMD_CLONE):
            audio_url = receive_text[len(CMD_CLONE):].strip()
            if not audio_url:
                await ctx.reply([Plain("请提供语音URL")])
                ctx.prevent_default()
                return
                
            result = await self._clone_voice(sender_id, audio_url)
            await ctx.reply([Plain(result)])
            ctx.prevent_default()
            return
            
        # 处理克隆命令（空格版本）
        if receive_text.startswith(CMD_CLONE_SPACE):
            audio_url = receive_text[len(CMD_CLONE_SPACE):].strip()
            if not audio_url:
                await ctx.reply([Plain("请提供语音URL")])
                ctx.prevent_default()
                return
                
            result = await self._clone_voice(sender_id, audio_url)
            await ctx.reply([Plain(result)])
            ctx.prevent_default()
            return
            
        # 处理简短克隆命令
        if receive_text.startswith(CMD_SHORT_CLONE):
            text = receive_text[len(CMD_SHORT_CLONE):].strip()
            if not text:
                await ctx.reply([Plain("请提供要说的文本内容")])
                ctx.prevent_default()
                return
                
            # 检查用户是否已克隆声音
            if sender_id not in self.voice_ids:
                await ctx.reply([Plain("您还没有克隆声音，请先使用'克隆声音：<语音URL>'命令")])
                ctx.prevent_default()
                return
                
            # 使用已克隆的声音生成语音
            result = await self._speak_text(sender_id, text)
            if isinstance(result, str):
                await ctx.reply([Plain(result)])
            else:
                await ctx.reply([result])
                self.logger.info("语音消息已发送")
            ctx.prevent_default()
            return
            
        # 处理说话命令
        if receive_text.startswith(CMD_SPEAK):
            text = receive_text[len(CMD_SPEAK):].strip()
            if not text:
                await ctx.reply([Plain("请提供要说的文本内容")])
                ctx.prevent_default()
                return
                
            result = await self._speak_text(sender_id, text)
            if isinstance(result, str):
                await ctx.reply([Plain(result)])
            else:
                await ctx.reply([result])
                self.logger.info("语音消息已发送")
            ctx.prevent_default()
            return

    @handler(GroupNormalMessageReceived)
    async def group_normal_message_received(self, ctx: EventContext):
        """处理群消息"""
        receive_text = ctx.event.text_message
        sender_id = ctx.event.sender_id
        
        self.logger.info(f"收到群消息: {receive_text}, sender_id={sender_id}")
        
        # 去除消息前后的空格
        receive_text = receive_text.strip()
        
        # 处理测试命令
        if receive_text == CMD_TEST:
            self.logger.info("执行测试命令，先返回克隆成功消息")
            clone_result = await self._clone_voice(sender_id, TEST_VOICE_URL)
            if "成功" not in clone_result:
                self.logger.error(f"克隆失败: {clone_result}")
                await ctx.reply([Plain(clone_result)])
                ctx.prevent_default()
                return
                
            # 返回克隆成功消息
            self.logger.info(f"返回克隆成功消息: {clone_result}")
            # 先发送文本消息
            await ctx.reply([Plain(clone_result)])
            
            # 生成测试语音
            self.logger.info("开始生成测试语音消息")
            voice_result = await self._speak_text(sender_id, TEST_TEXT)
            
            if isinstance(voice_result, str):
                # 错误情况
                self.logger.error(f"测试语音生成失败: {voice_result}")
                await ctx.reply([Plain(voice_result)])
            else:
                # 成功情况 - 直接使用Voice对象
                self.logger.info("测试语音消息生成成功，准备发送")
                # 单独发送语音消息
                await ctx.reply([voice_result])
                self.logger.info("语音消息已发送")
            
            ctx.prevent_default()
            return
            
        # 处理帮助命令
        if receive_text == CMD_HELP:
            await ctx.reply([Plain(HELP_TEXT)])
            ctx.prevent_default()
            return
            
        # 处理克隆命令（冒号版本）
        if receive_text.startswith(CMD_CLONE):
            audio_url = receive_text[len(CMD_CLONE):].strip()
            if not audio_url:
                await ctx.reply([Plain("请提供语音URL")])
                ctx.prevent_default()
                return
                
            result = await self._clone_voice(sender_id, audio_url)
            await ctx.reply([Plain(result)])
            ctx.prevent_default()
            return
            
        # 处理克隆命令（空格版本）
        if receive_text.startswith(CMD_CLONE_SPACE):
            audio_url = receive_text[len(CMD_CLONE_SPACE):].strip()
            if not audio_url:
                await ctx.reply([Plain("请提供语音URL")])
                ctx.prevent_default()
                return
                
            result = await self._clone_voice(sender_id, audio_url)
            await ctx.reply([Plain(result)])
            ctx.prevent_default()
            return
            
        # 处理简短克隆命令
        if receive_text.startswith(CMD_SHORT_CLONE):
            text = receive_text[len(CMD_SHORT_CLONE):].strip()
            if not text:
                await ctx.reply([Plain("请提供要说的文本内容")])
                ctx.prevent_default()
                return
                
            # 检查用户是否已克隆声音
            if sender_id not in self.voice_ids:
                await ctx.reply([Plain("您还没有克隆声音，请先使用'克隆声音：<语音URL>'命令")])
                ctx.prevent_default()
                return
                
            # 使用已克隆的声音生成语音
            result = await self._speak_text(sender_id, text)
            if isinstance(result, str):
                await ctx.reply([Plain(result)])
            else:
                await ctx.reply([result])
                self.logger.info("语音消息已发送")
            ctx.prevent_default()
            return
            
        # 处理说话命令
        if receive_text.startswith(CMD_SPEAK):
            text = receive_text[len(CMD_SPEAK):].strip()
            if not text:
                await ctx.reply([Plain("请提供要说的文本内容")])
                ctx.prevent_default()
                return
                
            result = await self._speak_text(sender_id, text)
            if isinstance(result, str):
                await ctx.reply([Plain(result)])
            else:
                await ctx.reply([result])
                self.logger.info("语音消息已发送")
            ctx.prevent_default()
            return

    async def _handle_command(self, sender_id: int, command: str, params: List[str]) -> Union[str, str]:
        """处理命令"""
        self.logger.info(f"处理命令: sender_id={sender_id}, command={command}, params={params}")
        
        if command == CMD_CLONE:
            if not params:
                return "请提供语音URL"
            return await self._clone_voice(sender_id, params[0])

        elif command == CMD_SPEAK:
            if not params:
                return "请提供要说的文本内容"
            return await self._speak_text(sender_id, " ".join(params))

        elif command == CMD_HELP:
            return self._get_help()

        return '无效指令，请输入"!voice 帮助"查看帮助'

    def _generate_prefix(self, sender_id: int) -> str:
        """生成不超过10个字符的prefix"""
        return f"u{str(sender_id)[-4:]}"

    async def _clone_voice(self, sender_id: int, audio_url: str) -> str:
        """克隆声音"""
        self.logger.info(f"开始克隆声音: sender_id={sender_id}, url={audio_url}")
        try:
            service = VoiceEnrollmentService()
            self.logger.info("创建VoiceEnrollmentService实例")
            
            self.logger.info(f"调用声音克隆API: target_model={self.target_model}, prefix={self._generate_prefix(sender_id)}")
            voice_id = service.create_voice(
                target_model=self.target_model,
                prefix=self._generate_prefix(sender_id),
                url=audio_url
            )
            self.logger.info(f"声音克隆API调用成功: voice_id={voice_id}, request_id={service.get_last_request_id()}")
            
            self.voice_ids[sender_id] = voice_id
            self.logger.info(f"保存voice_id成功: {self.voice_ids}")
            
            # 保存用户设置
            self._save_user_settings()
            
            return f"声音克隆成功！Voice ID: {voice_id}"
        except Exception as e:
            self.logger.error(f"声音克隆失败: {str(e)}", exc_info=True)
            return f"声音克隆失败: {str(e)}"

    def convert_to_silk(self, wav_file: str) -> str:
        """将WAV文件转换为SILK格式"""
        temp_folder = os.path.join(os.path.dirname(__file__), "temp")
        silk_path = os.path.join(temp_folder, Path(wav_file).stem + ".silk")
        
        try:
            silkcoder.encode(wav_file, silk_path)
            self.logger.info(f"已将 WAV 文件 {wav_file} 转换为 SILK 文件 {silk_path}")
            return silk_path
        except Exception as e:
            self.logger.error(f"SILK 文件转换失败: {str(e)}")
            return None

    async def _speak_text(self, sender_id: int, text: str) -> Union[str, Voice]:
        """使用克隆的声音说话"""
        self.logger.info(f"开始生成语音: sender_id={sender_id}, text={text}")
        
        if sender_id not in self.voice_ids:
            self.logger.warning(f"用户 {sender_id} 未克隆声音")
            return "请先克隆一个声音"

        try:
            # 使用克隆的声音进行语音合成
            voice_id = self.voice_ids[sender_id]
            self.logger.info(f"准备语音合成: model={self.target_model}, voice_id={voice_id}")
            synthesizer = SpeechSynthesizer(model=self.target_model, voice=voice_id)
            
            self.logger.info(f"调用语音合成API，文本内容: {text}")
            audio = synthesizer.call(text)
            self.logger.info(f"语音合成API调用成功: request_id={synthesizer.get_last_request_id()}")
            self.logger.info(f"获取到音频数据，大小: {len(audio)} 字节")

            # 创建临时目录
            temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
            os.makedirs(temp_dir, exist_ok=True)
            
            # 保存WAV文件
            wav_file = os.path.join(temp_dir, "temp.wav")
            self.logger.info(f"准备保存WAV文件: {wav_file}")
            
            try:
                with open(wav_file, "wb") as f:
                    f.write(audio)
                if os.path.getsize(wav_file) == 0:
                    raise ValueError("生成的WAV文件为空")
            except Exception as e:
                self.logger.error(f"保存WAV文件失败: {e}")
                return f"文件保存失败: {str(e)}"
                
            self.logger.info(f"WAV文件保存成功，大小: {os.path.getsize(wav_file)} 字节")
            
            # 转换为silk格式
            silk_file = self.convert_to_silk(wav_file)
            if not silk_file or not os.path.exists(silk_file):
                self.logger.error("SILK文件生成失败")
                return "语音转换失败"

            # 创建Voice对象
            try:
                self.logger.info(f"创建Voice对象，使用路径: {silk_file}")
                voice_msg = Voice(path=str(silk_file))
                # 清理临时文件
                try:
                    os.remove(wav_file)
                    self.logger.info(f"清理WAV临时文件成功: {wav_file}")
                except Exception as e:
                    self.logger.warning(f"清理WAV临时文件失败: {e}")
                return voice_msg
            except Exception as e:
                self.logger.error(f"创建Voice对象失败: {e}")
                return f"语音消息创建失败: {str(e)}"

        except Exception as e:
            self.logger.error("语音合成失败", exc_info=True)
            return f"语音合成失败: {str(e)}"

    def _get_help(self) -> str:
        """获取帮助信息"""
        return HELP_TEXT

    def __del__(self):
        """插件卸载时触发"""
        # 保存用户设置
        self._save_user_settings()
        
        temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"临时目录已清理: {temp_dir}")
            except Exception as e:
                logger.warning(f"清理临时目录失败: {e}")
