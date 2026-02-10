from openai import OpenAI  

# 必填：从服务管控页面获取对应服务的APIKey和API Base
api_key = "<YOUR_API_KEY>"
api_base = "http://maas-api.cn-huabei-1.xf-yun.com/v1"

client = OpenAI(api_key=api_key, base_url=api_base)

def unified_chat_test(model_id, messages, use_stream=False, extra_body={}):
    """
    一个统一的函数，用于演示多种调用场景。

    :param model_id: 要调用的模型ID。
    :param messages: 对话消息列表。
    :param use_stream: 是否使用流式输出。
    :param extra_body: 包含额外请求参数的字典，如 response_format。
    """
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=use_stream,
            temperature=0.7,
            max_tokens=4096,
            extra_headers={"lora_id": "0"},  # 调用微调大模型时,对应替换为模型服务卡片上的resourceId
            stream_options={"include_usage": True},
            extra_body=extra_body
        )

        if use_stream:
            # 处理流式响应
            full_response = ""
            print("--- 流式输出 ---")
            for chunk in response:
                if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    print(content, end="", flush=True)
                    full_response += content
            print("\n\n--- 完整响应 ---")
            print(full_response)
        else:
            # 处理非流式响应
            print("--- 非流式输出 ---")
            message = response.choices[0].message
            print(message.content)

    except Exception as e:
        print(f"请求出错: {e}")

if __name__ == "__main__":
    model_id = "<YOUR_MODEL_ID>" # 必填：调用大模型时，对应为推理服务的模型卡片上对应的modelId

    # 1. 普通非流式调用
    print("********* 1. 普通非流式调用 *********")
    plain_messages = [{"role": "user", "content": "你好，请介绍一下自己。"}]
    unified_chat_test(model_id, plain_messages, use_stream=False)

    # 2. 普通流式调用
    print("\n********* 2. 普通流式调用 *********")
    stream_messages = [{"role": "user", "content": "写一首关于夏天的诗。"}]
    unified_chat_test(model_id, stream_messages, use_stream=True)

    # 3. JSON Mode 调用
    print("\n********* 3. JSON Mode 调用 *********")
    json_messages = [{"role": "user", "content": "请给我一个关于上海的JSON对象，包含城市名称(city)和人口数量(population)。"}]
    json_extra_body = {
        "response_format": {"type": "json_object"},
        "search_disable": True # JSON Mode下建议关闭搜索
    }
    unified_chat_test(model_id, json_messages, use_stream=False, extra_body=json_extra_body)

    # 4. 测试stop和前缀续写功能
    print("\n********* 4. 测试stop和前缀续写功能 *********")
    print("设置stop词: ['。', '！'] - 模型遇到句号或感叹号时会停止生成")
    stream_messages = [{"role": "user", "content": "给我解释下1加1等于多少。"}]
    unified_chat_test(model_id, stream_messages, use_stream=True, extra_body={"stop": ["。","！"],"continue_final_message":True})

    # 5. Tools/Function Calling 调用示例
    print("\n********* 5. Tools/Function Calling 调用示例 *********")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定城市的天气信息",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "城市名称，例如：北京、上海"
                        },
                        "unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "温度单位"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    ]
    tool_messages = [{"role": "user", "content": "北京今天天气怎么样？"}]
    response = client.chat.completions.create(
        model=model_id,
        messages=tool_messages,
        tools=tools,
        tool_choice="auto"
    )
    message = response.choices[0].message
    if message.tool_calls:
        print(f"模型请求调用工具: {message.tool_calls[0].function.name}")
        print(f"参数: {message.tool_calls[0].function.arguments}")
    else:
        print(message.content)


2.2 请求参数
参数	类型	是否必填	要求	说明
model	string	是		指定要调用的对话生成模型ID
messages	array	是	[{"role": "user", "content":"用户输入内容"}]	表示对话上下文的消息列表，支持多轮对话交互。其中，role 用于标识消息发送方（例如 user 表示用户、assistant 表示模型回复），content 则为实际文本内容。 注意：仅限DeepSeekV3&R1使用对话前缀续写时，用户需确保 messages 列表里最后一条消息的 role 为 assistant
stream	boolean	否	取值为 true 或 false，默认值为 false	指定是否采用流式响应模式。若设置为 true，系统将逐步返回生成的回复内容；否则，将一次性返回完整响应
temperature	float	否	取值为[0,1],默认值为0.7	核采样阈值。用于决定结果随机性，取值越高随机性越强即相同的问题得到的不同答案的可能性越高
max_tokens	int	否	取值为[1,32768]，默认值为2048	限制生成回复的最大 token 数量，不同模型限制生成内容的最大 token 数有差异，DeepSeekV3&R1 支持最大 32k，其他模型默认上限 8K。
reasoning_effort	string	否	low, medium, high, 默认high	指导模型在对提示做出响应之前应生成多少推理内容。low 优先考虑速度和节省token，high 优先考虑更完整的推理。仅针对OpenAI开源的OSS模型生效。注意：使用 OpenAI SDK 时，此参数需放在 extra_body 对象中。
lora_id	string	否	调用微调模型时使用，对应模型服务卡片上的 resourceId。注意：使用 OpenAI SDK 时，此参数需放在 extra_headers 对象中。	
stop	string[]	否	仅限DeepSeekV3&R1支持	模型遇到 stop 字段所指定的字符串时将停止继续生成，这个词语本身不会输出。最多支持4个字符串如["你好”,"天气”]
continue_final_message	boolean	否	仅限DeepSeekV3&R1支持，取值为 true 或 false，默认值为 false	指定是否开启对话前缀续写功能，若设置为true，系统将对 messages 列表里最后一条消息的 role 为 assistant的内容进行续写。
search_disable	boolean	否	true 或 false，默认 true	关闭联网搜索。注意：使用 OpenAI SDK 时，此参数需放在 extra_body 对象中。
show_ref_label	boolean	否	默认为 false。true 表示在联网搜索时返回信源信息。注意：使用 OpenAI SDK 时，此参数需放在 extra_body 对象中。	
enable_thinking	boolean	否	默认为 false。true 表示开启深度思考模式（仅部分模型支持）。注意：使用 OpenAI SDK 时，此参数需放在 extra_body 对象中。	
response_format	object	否	用于指定模型的输出为 JSON 对象格式。设置为 {"type": "json_object"} 即可。仅支持 DeepSeek R1 和 V3 模型。详情见 2.2.2 response_format 参数说明。	
tools	array	否	工具列表，支持 function 类型的工具定义	模型可能会调用的工具列表，用于 Function Calling 功能。注意：目前仅支持 DeepSeek V3.2 和 GLM-4.7 系列模型。详情见 2.2.3 tools 参数说明。
tool_choice	string	否	auto、none、required，默认为 auto	控制模型如何选择和使用工具。auto 表示模型自主决策；none 表示禁用工具调用；required 表示必须调用至少一个工具。
stream_options	object	否	默认值为{"include_usage": True}	针对流式响应模式的扩展配置，如控制是否在响应中包含API调用统计信息等附加数据。