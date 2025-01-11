<div align="center">

<div style="width: 10;>

[![haystack666/WeChatQRLogin](https://haydata-cd.oss-cn-chengdu.aliyuncs.com/github/TimedReminder/logo.png?x-oss-process=style/WeChatQRLogin_image_small)](https://github.com/haystack666/TimedReminder)

</div>

</div>

<h1 align="center">TimedReminder / 去你妈的定时任务</h1>


内容和项目名一样

部分功能也许仍在开发🚧，可以期待一下


使用python后端，mysql数据库


---
### 1.运行之前先装一下必要组件


```shell
pip install flask flask_sqlalchemy schedule requests PyMySQL bcrypt
```


---
### 2.`main.py`里面有一些地方需要改成自己的，大概在22行的样子，比如


```python
app.config['SECRET_KEY'] = '随便输入一些英文加数字东西，也可以使用代码随机生成'

DB_USER = '数据库用户名'
DB_PASS = '数据库密码'
DB_HOST = '数据库地址'
DB_NAME = '数据库名'
```


----
### 3.运行项目


```shell
python3 main.py
```

使用进程守护supervisor（其他的也行），防止进程死掉，项目运行在`8710`端口，如果存在端口冲突可以在`main.py`文件的最后来修改为你自己喜欢的端口


```python
app.run(host='0.0.0.0', port=<改成你喜欢的>, debug=False)
```

