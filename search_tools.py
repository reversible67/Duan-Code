from ddgs import DDGS

def web_search(query : str, max_results : int = 3) -> str:
    """
    联网搜索工具（ddgs）
    无需API Key，国内可直接使用
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return "搜索结果为空"
        
        output = []
        for idx, res in enumerate(results, 1):
            title = res.get("title", "无标题")
            body = res.get("body", "无摘要")
            output.append(f"【结果{idx}】\n标题：{title}\n内容：{body}\n")
    
        return "\n".join(output)
    
    except Exception as e:
        return f"搜索失败：{str(e)}"