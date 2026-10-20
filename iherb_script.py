from jev_ultrafast import Agent

with Agent(
    "https://cn.iherb.com",
    "把sports resarch 阿拉斯加90粒鱼油添加到购物车，依次输入折扣码 ITG9175 和 26moon25， 收货地址是 牛牛 18215547995 四川省成都市武侯区浆洗街街道一环路西一段2号高升大厦7楼706室多康体育dksc体能训练中心，邮编设置为000000 ，发起微信支付，我来扫码支付",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
