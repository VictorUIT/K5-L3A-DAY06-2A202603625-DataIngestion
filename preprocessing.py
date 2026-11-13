def create_units_from_sections(sections_data, min_chars=1500):
    """
    sections_data: Danh sách các dict gồm {'title': ..., 'content': ...}
    """
    units = []
    current_unit_content = ""
    current_titles = []
    
    for section in sections_data:
        current_unit_content += "\n" + section['content']
        current_titles.append(section['title'])
        
        # Khi tích lũy đủ >= 1500 ký tự thì chốt thành 1 unit
        if len(current_unit_content) >= min_chars:
            units.append({
                "unit_id": f"unit_{len(units) + 1}",
                "titles": " > ".join(current_titles),
                "content": current_unit_content.strip(),
                "char_count": len(current_unit_content.strip())
            })
            current_unit_content = ""
            current_titles = []
            
-    # Xử lý phần dư cuối cùng nếu còn
    if current_unit_content and len(current_unit_content) > 200: # Tránh rác quá nhỏ
        if units:
            # Gộp vào unit cuối nếu unit cuối quá ngắn, hoặc tạo unit mới tùy ý
            units[-1]["content"] += "\n" + current_unit_content.strip()
            units[-1]["char_count"] = len(units[-1]["content"])
            
    return units