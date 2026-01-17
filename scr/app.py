from flask import Flask, render_template, session, request, redirect, url_for, flash, jsonify
import pymysql as db
from config import host, user, password, port, db_name
import uuid
from datetime import datetime, timedelta
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'softkey_secret_key'

# Папка, куда будут сохраняться изображения товаров (должна существовать!)
UPLOAD_FOLDER = os.path.join('static', 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
            filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    try:
        return db.connect(
            host=host, port=port, user=user, password=password,
            database=db_name, cursorclass=db.cursors.DictCursor, autocommit=True
        )
    except Exception as ex:
        print("Ошибка подключения:", ex)
        return None

@app.route('/index')
@app.route('/')
def index():
    category_id = request.args.get('category')
    search_query = request.args.get('search')
    sort_price = request.args.get('sort_price') # asc / desc
    sort_date = request.args.get('sort_date')   # new / old
    
    conn = get_db_connection()
    categories = []
    products = []
    
    if conn:
        with conn.cursor() as cursor:
            # Получаем все категории для выпадающего списка
            cursor.execute("SELECT * FROM Категории")
            categories = cursor.fetchall()
            
            # Строим запрос
            sql = "SELECT t.*, k.Название_категории FROM Товары t JOIN Категории k ON t.ID_Категории = k.ID_Категории WHERE Статус_активности = 1"
            params = []
            
            if category_id and category_id != 'all':
                sql += " AND t.ID_Категории = %s"
                params.append(category_id)
            if search_query:
                sql += " AND t.Название LIKE %s"
                params.append(f"%{search_query}%")
            
            # Сортировка
            order_clauses = []
            if sort_price == 'asc': order_clauses.append("t.Цена ASC")
            elif sort_price == 'desc': order_clauses.append("t.Цена DESC")
            
            if sort_date == 'new': order_clauses.append("t.ID_Товара DESC")
            elif sort_date == 'old': order_clauses.append("t.ID_Товара ASC")
            
            if order_clauses:
                sql += " ORDER BY " + ", ".join(order_clauses)
                
            cursor.execute(sql, tuple(params))
            products = cursor.fetchall()
        conn.close()
    
    return render_template('index.html', products=products, categories=categories)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form['login']
        password_input = request.form['password']
        
        conn = get_db_connection()
        if conn:
            with conn.cursor() as cursor:
                # Обязательно выбираем ID_Роли
                sql = "SELECT * FROM Пользователи WHERE Логин = %s AND Пароль = %s"
                cursor.execute(sql, (login_input, password_input))
                user = cursor.fetchone()
                
                if user:
                    # Сохраняем данные в сессию
                    session['user_id'] = user['ID_Пользователя']
                    session['role_id'] = user['ID_Роли'] # Сохраняем ID роли
                    session['user_name'] = user['Имя']
                    
                    flash(f"Добро пожаловать, {user['Имя']}!", "success")
                    
                    # ПЕРЕНАПРАВЛЕНИЕ ПО РОЛИ
                    if user['ID_Роли'] == 1:
                        return redirect(url_for('admin_dashboard')) # В админку
                    else:
                        return redirect(url_for('index')) # На главную для клиентов
                else:
                    flash("Неверный логин или пароль", "error")
            conn.close()
            
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password_val = request.form['password']
        name = request.form.get('name', 'Пользователь') 
        
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    # Проверяем, нет ли уже такого логина
                    cursor.execute("SELECT * FROM Пользователи WHERE Логин = %s", (email,))
                    if cursor.fetchone():
                        flash('Пользователь с такой почтой уже существует', 'error')
                    else:
                        # ID_Роли = 2 (Обычный клиент), Фамилия обязательна по БД
                        sql = "INSERT INTO Пользователи (Фамилия, Имя, Логин, Пароль, ID_Роли) VALUES (%s, %s, %s, %s, %s)"
                        cursor.execute(sql, ('Клиент', name, email, password_val, 2))
                        flash('Регистрация успешна! Теперь войдите.', 'success')
                        return redirect(url_for('login'))
            except Exception as e:
                flash(f'Ошибка: {str(e)}', 'error')
            finally:
                conn.close()
    return render_template('register.html')

# --- ОТОБРАЖЕНИЕ КОРЗИНЫ ---
@app.route('/cart')
def cart():
    if 'user_id' not in session:
        flash("Войдите, чтобы пользоваться корзиной", "error")
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    items = []
    total_price = 0
    if conn:
        with conn.cursor() as cursor:
            sql = """
                SELECT k.*, t.Название, t.Цена, t.Описание, t.Изображение, cat.Название_категории
                FROM Корзина k
                JOIN Товары t ON k.ID_Товара = t.ID_Товара
                JOIN Категории cat ON t.ID_Категории = cat.ID_Категории
                WHERE k.ID_Пользователя = %s
            """
            cursor.execute(sql, (session['user_id'],))
            items = cursor.fetchall()
            # Считаем сумму
            total_price = sum(item['Цена'] * item['Количество'] for item in items)
        conn.close()
    return render_template('cart.html', items=items, total_price=total_price)

# --- ИЗМЕНЕНИЕ КОЛИЧЕСТВА ---
@app.route('/cart/update/<int:product_id>/<action>')
def update_cart(product_id, action):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            if action == 'plus':
                cursor.execute("UPDATE Корзина SET Количество = Количество + 1 WHERE ID_Пользователя = %s AND ID_Товара = %s", (session['user_id'], product_id))
            elif action == 'minus':
                # Удаляем, если количество станет 0
                cursor.execute("UPDATE Корзина SET Количество = Количество - 1 WHERE ID_Пользователя = %s AND ID_Товара = %s AND Количество > 1", (session['user_id'], product_id))
        conn.close()
    return redirect(url_for('cart'))

# --- УДАЛЕНИЕ ИЗ КОРЗИНЫ ---
@app.route('/cart/remove/<int:product_id>')
def remove_from_cart(product_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM Корзина WHERE ID_Пользователя = %s AND ID_Товара = %s", (session['user_id'], product_id))
        conn.close()
    return redirect(url_for('cart'))


@app.route('/checkout', methods=['POST'])
def checkout():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # 1. Получаем товары из корзины
                cursor.execute("""
                    SELECT k.ID_Товара, k.Количество, t.Цена 
                    FROM Корзина k 
                    JOIN Товары t ON k.ID_Товара = t.ID_Товара
                    WHERE k.ID_Пользователя = %s
                """, (session['user_id'],))
                cart_items = cursor.fetchall()
                
                if not cart_items:
                    flash("Корзина пуста", "error")
                    return redirect(url_for('cart'))

                # 2. Считаем общую сумму
                total_sum = sum(item['Цена'] * item['Количество'] for item in cart_items)

                # 3. Создаем основной заказ
                cursor.execute("INSERT INTO Заказы (ID_Пользователя, Дата_заказа, Статус, Итоговая_сумма) VALUES (%s, NOW(), 'Оплачен', %s)", 
                                (session['user_id'], total_sum))
                order_id = cursor.lastrowid

                # 4. Для каждого товара в корзине...
                for item in cart_items:
                    # Вставляем в Состав_заказа
                    cursor.execute("""
                        INSERT INTO Состав_заказа (ID_Заказа, ID_Товара, Цена_продажи, Срок_лицензии_дни, Количество)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (order_id, item['ID_Товара'], item['Цена'], 365, item['Количество']))
                    
                    pos_id = cursor.lastrowid # ID только что созданной строки в составе заказа

                    # 5. Генерируем лицензионные ключи (по одному на каждую единицу товара)
                    for _ in range(item['Количество']):
                        new_key = str(uuid.uuid4()).upper()[:18] # Пример: 123E4567-E89B-12D3
                        # Рассчитываем дату истечения (например, + год)
                        expire_date = datetime.now() + timedelta(days=365)
                        
                        cursor.execute("""
                            INSERT INTO Лицензии (ID_Позиции_заказа, Лицензионный_ключ, Дата_активации, Дата_истечения)
                            VALUES (%s, %s, NOW(), %s)
                        """, (pos_id, new_key, expire_date))

                # 6. Очищаем корзину
                cursor.execute("DELETE FROM Корзина WHERE ID_Пользователя = %s", (session['user_id'],))
            
            flash(f"Заказ #{order_id} успешно оформлен! Ключи созданы.", "success")
        except Exception as ex:
            print("Ошибка оформления:", ex)
            flash("Ошибка при создании заказа", "error")
        finally:
            conn.close()
            
    return redirect(url_for('orders'))

@app.route('/product/<int:product_id>')
def product_detail(product_id):
    conn = get_db_connection()
    product = None
    
    if conn:
        with conn.cursor() as cursor:
            sql = """
                SELECT t.*, k.Название_категории
                FROM Товары t
                JOIN Категории k ON t.ID_Категории = k.ID_Категории
                WHERE t.ID_Товара = %s
            """
            cursor.execute(sql, (product_id,))
            product = cursor.fetchone()
        conn.close()
    
    if not product:
        flash("Товар не найден", "error")
        return redirect(url_for('index'))
        
    return render_template('product_detail.html', product=product)


# --- ДОБАВЛЕНИЕ В КОРЗИНУ ---
@app.route('/add_to_cart/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    if 'user_id' not in session:
        flash("Войдите в аккаунт, чтобы добавить товар в корзину", "error")
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # Проверяем, есть ли уже такой товар в корзине у этого пользователя
                cursor.execute("SELECT * FROM Корзина WHERE ID_Пользователя = %s AND ID_Товара = %s", 
                                (session['user_id'], product_id))
                item = cursor.fetchone()
                
                if item:
                    # Если есть — увеличиваем количество
                    cursor.execute("UPDATE Корзина SET Количество = Количество + 1 WHERE ID_Пользователя = %s AND ID_Товара = %s", 
                                    (session['user_id'], product_id))
                else:
                    # Если нет — добавляем новую запись
                    cursor.execute("INSERT INTO Корзина (ID_Пользователя, ID_Товара, Количество) VALUES (%s, %s, 1)", 
                                    (session['user_id'], product_id))
            
            flash("Товар добавлен в корзину!", "success")
        except Exception as ex:
            print("Ошибка добавления в корзину:", ex)
            flash("Не удалось добавить товар", "error")
        finally:
            conn.close()
            
    # Возвращаемся обратно в корзину или на ту же страницу
    return redirect(url_for('cart'))

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    user_data = None
    orders = []
    
    if conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM Пользователи WHERE ID_Пользователя = %s", (session['user_id'],))
            user_data = cursor.fetchone()
            
            cursor.execute("SELECT * FROM Заказы WHERE ID_Пользователя = %s ORDER BY Дата_заказа DESC", (session['user_id'],))
            orders = cursor.fetchall()
        conn.close()
    
    # Рассчитываем максимальную дату (сегодня) для ограничения выбора даты рождения
    from datetime import date
    max_date = date.today().isoformat()
    
    # Функция для форматирования телефона
    def format_phone(phone):
        if not phone:
            return ''
        cleaned = ''.join(filter(str.isdigit, str(phone)))
        if len(cleaned) == 11 and cleaned.startswith('7'):
            return f"+7({cleaned[1:4]})-{cleaned[4:7]}-{cleaned[7:9]}-{cleaned[9:11]}"
        return phone
    
    return render_template('profile.html',
                        user=user_data,
                        orders=orders,
                        formatPhone=format_phone,
                        max_date=max_date)

# --- ВЫХОД ИЗ СИСТЕМЫ ---
@app.route('/logout')
def logout():
    session.clear() # Полная очистка сессии
    flash("Вы вышли из системы", "success")
    return redirect(url_for('index'))

# --- ОБНОВЛЕНИЕ ЛИЧНЫХ ДАННЫХ ---
@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Получаем данные из формы
    last_name = request.form.get('last_name')
    first_name = request.form.get('first_name')
    middle_name = request.form.get('middle_name')
    phone = request.form.get('phone')
    birth_date = request.form.get('birth_date')
    
    # Очищаем телефон от форматирования (оставляем только цифры)
    if phone:
        phone = ''.join(filter(str.isdigit, phone))
        # Если номер начинается с +7, оставляем 7
        if phone.startswith('7'):
            phone = phone
        elif len(phone) == 10:
            phone = '7' + phone
        elif len(phone) == 11 and phone.startswith('8'):
            phone = '7' + phone[1:]
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # Проверяем, не занят ли телефон другим пользователем
                if phone:
                    cursor.execute("SELECT ID_Пользователя FROM Пользователи WHERE Телефон = %s AND ID_Пользователя != %s", 
                                (phone, session['user_id']))
                    if cursor.fetchone():
                        flash("Этот телефон уже используется другим пользователем", "error")
                        return redirect(url_for('profile'))
                
                # Обновляем только разрешенные поля (логин не трогаем)
                sql = """
                    UPDATE Пользователи 
                    SET Фамилия = %s, Имя = %s, Отчество = %s, Телефон = %s, Дата_рождения = %s
                    WHERE ID_Пользователя = %s
                """
                cursor.execute(sql, (last_name, first_name, middle_name, phone, birth_date, session['user_id']))
            flash("Данные успешно обновлены!", "success")
        except Exception as ex:
            print("Ошибка при обновлении профиля:", ex)
            flash("Ошибка при обновлении данных", "error")
        finally:
            conn.close()
            
    return redirect(url_for('profile'))

# --- СМЕНА ПАРОЛЯ ---
@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    current_password = request.form.get('current_password')
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')
    
    # Валидация на стороне сервера
    if not current_password or not new_password or not confirm_password:
        flash("Все поля обязательны для заполнения", "error")
        return redirect(url_for('profile'))
    
    if len(new_password) < 8:
        flash("Пароль должен содержать минимум 8 символов", "error")
        return redirect(url_for('profile'))
    
    # Проверка сложности пароля (хотя бы одна буква и одна цифра)
    if not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
        flash("Пароль должен содержать буквы и цифры", "error")
        return redirect(url_for('profile'))
    
    if new_password != confirm_password:
        flash("Пароли не совпадают", "error")
        return redirect(url_for('profile'))
    
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # Проверяем текущий пароль
                cursor.execute("SELECT Пароль FROM Пользователи WHERE ID_Пользователя = %s",
                            (session['user_id'],))
                user_data = cursor.fetchone()
                
                if not user_data:
                    flash("Пользователь не найден", "error")
                    return redirect(url_for('profile'))
                
                # Проверяем, совпадает ли текущий пароль
                if user_data['Пароль'] != current_password:
                    flash("Неверный текущий пароль", "error")
                    return redirect(url_for('profile'))
                
                # Проверяем, не совпадает ли новый пароль с текущим
                if new_password == current_password:
                    flash("Новый пароль должен отличаться от текущего", "error")
                    return redirect(url_for('profile'))
                
                # Обновляем пароль
                sql = "UPDATE Пользователи SET Пароль = %s WHERE ID_Пользователя = %s"
                cursor.execute(sql, (new_password, session['user_id']))
                
                flash("Пароль успешно изменен!", "success")
                
        except Exception as ex:
            print("Ошибка при смене пароля:", ex)
            flash("Ошибка при смене пароля", "error")
        finally:
            conn.close()
    
    return redirect(url_for('profile'))

# --- УДАЛЕНИЕ АККАУНТА ---
@app.route('/delete_account')
def delete_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # Важно: если в БД нет каскадного удаления,
                # сначала нужно удалить товары из корзины этого пользователя
                cursor.execute("DELETE FROM Корзина WHERE ID_Пользователя = %s", (user_id,))
                # Затем удаляем самого пользователя
                cursor.execute("DELETE FROM Пользователи WHERE ID_Пользователя = %s", (user_id,))
            
            session.clear()
            flash("Ваш аккаунт был полностью удален", "success")
            return redirect(url_for('index'))
        except Exception as ex:
            print("Ошибка при удалении:", ex)
            flash("Не удалось удалить аккаунт (возможно, у вас есть активные заказы)", "error")
        finally:
            conn.close()
            
    return redirect(url_for('profile'))

@app.route('/orders')
def orders():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    orders_list = []
    
    if conn:
        with conn.cursor() as cursor:
            # Получаем заказы, товары и их ключи одним запросом
            cursor.execute("""
                SELECT
                    z.ID_Заказа, z.Дата_заказа, z.Статус, z.Итоговая_сумма,
                    sz.ID_Позиции, sz.Количество, sz.Цена_продажи,
                    t.Название,
                    l.Лицензионный_ключ
                FROM Заказы z
                JOIN Состав_заказа sz ON z.ID_Заказа = sz.ID_Заказа
                JOIN Товары t ON sz.ID_Товара = t.ID_Товара
                LEFT JOIN Лицензии l ON sz.ID_Позиции = l.ID_Позиции_заказа
                WHERE z.ID_Пользователя = %s
                ORDER BY z.Дата_заказа DESC
            """, (session['user_id'],))
            rows = cursor.fetchall()

            # Группируем данные: Заказ -> Позиции -> Ключи
            orders_dict = {}
            for row in rows:
                o_id = row['ID_Заказа']
                if o_id not in orders_dict:
                    orders_dict[o_id] = {
                        'ID_Заказа': o_id,
                        'Дата_заказа': row['Дата_заказа'],
                        'Статус': row['Статус'],
                        'Итоговая_сумма': row['Итоговая_сумма'],
                        'products_list': {} # Используем словарь для группировки по ID позиции
                    }
                
                pos_id = row['ID_Позиции']
                if pos_id not in orders_dict[o_id]['products_list']:
                    orders_dict[o_id]['products_list'][pos_id] = {
                        'Название': row['Название'],
                        'Количество': row['Количество'],
                        'Цена_продажи': row['Цена_продажи'],
                        'keys': []
                    }
                
                # Добавляем ключ, если он есть
                if row['Лицензионный_ключ']:
                    orders_dict[o_id]['products_list'][pos_id]['keys'].append(row['Лицензионный_ключ'])

            # Превращаем в список для шаблона
            for o_id in orders_dict:
                order = orders_dict[o_id]
                # Превращаем словарь продуктов обратно в список
                order['products_list'] = list(order['products_list'].values())
                orders_list.append(order)

        conn.close()
    
    # Получаем данные пользователя для шапки
    user_data = None
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM Пользователи WHERE ID_Пользователя = %s", (session['user_id'],))
            user_data = cursor.fetchone()
        conn.close()

    return render_template('orders.html', user=user_data, orders=orders_list)


@app.route('/admin')
def admin_dashboard():
    # Проверка на права админа (ID_Роли = 1)
    if 'user_id' not in session or session.get('role_id') != 1:
        flash("Доступ запрещен", "error")
        return redirect(url_for('index'))

    conn = get_db_connection()
    stats = {}
    products = []
    orders = []
    categories = []
    reports = {}
    if conn:
        with conn.cursor() as cursor:
            # 1. Считаем статистику
            cursor.execute("SELECT SUM(Итоговая_сумма) as total FROM Заказы WHERE Статус = 'Оплачен'")
            stats['revenue'] = cursor.fetchone()['total'] or 0
            
            cursor.execute("SELECT COUNT(*) as count FROM Заказы")
            stats['orders_count'] = cursor.fetchone()['count']
            
            cursor.execute("SELECT COUNT(*) as count FROM Товары")
            stats['products_count'] = cursor.fetchone()['count']
            
            cursor.execute("SELECT COUNT(*) as count FROM Пользователи")
            stats['users_count'] = cursor.fetchone()['count']

            # 2. Список товаров с категориями
            cursor.execute("""
                SELECT t.*, k.Название_категории 
                FROM Товары t 
                LEFT JOIN Категории k ON t.ID_Категории = k.ID_Категории
                WHERE t.Статус_активности = 1
            """)
            products = cursor.fetchall()

            cursor.execute("SELECT * FROM Категории")
            categories = cursor.fetchall()

            # 3. Список заказов с именами пользователей
            cursor.execute("""
                SELECT z.*, p.Имя, p.Фамилия 
                FROM Заказы z 
                JOIN Пользователи p ON z.ID_Пользователя = p.ID_Пользователя 
                ORDER BY z.Дата_заказа DESC
            """)
            orders = cursor.fetchall()
            
            # 1. ТОП-5 популярных товаров
            cursor.execute("""
                SELECT t.Название, SUM(sz.Количество) as total_qty
                FROM Состав_заказа sz
                JOIN Товары t ON sz.ID_Товара = t.ID_Товара
                GROUP BY t.ID_Товара
                ORDER BY total_qty DESC LIMIT 5
            """)
            reports['top_products'] = cursor.fetchall()

            # 2. Выручка по категориям
            cursor.execute("""
                SELECT k.Название_категории, SUM(sz.Цена_продажи * sz.Количество) as total_revenue
                FROM Состав_заказа sz
                JOIN Товары t ON sz.ID_Товара = t.ID_Товара
                JOIN Категории k ON t.ID_Категории = k.ID_Категории
                GROUP BY k.ID_Категории
                ORDER BY total_revenue DESC
            """)
            reports['category_revenue'] = cursor.fetchall()

            # 3. Активность продаж по дням (последние 14)
            cursor.execute("""
                SELECT DATE(Дата_заказа) as day, COUNT(ID_Заказа) as order_count, SUM(Итоговая_сумма) as daily_sum
                FROM Заказы
                WHERE Статус = 'Оплачен'
                GROUP BY day
                ORDER BY day DESC LIMIT 14
            """)
            reports['daily_sales'] = cursor.fetchall()

            # 4. ТОП-5 Покупателей (Самые ценные клиенты)
            cursor.execute("""
                SELECT p.Имя, p.Фамилия, p.Логин, SUM(z.Итоговая_сумма) as total_spent
                FROM Заказы z
                JOIN Пользователи p ON z.ID_Пользователя = p.ID_Пользователя
                WHERE z.Статус = 'Оплачен'
                GROUP BY p.ID_Пользователя
                ORDER BY total_spent DESC LIMIT 5
            """)
            reports['vip_customers'] = cursor.fetchall()
            
        conn.close()
    
    return render_template('admin.html', stats=stats, products=products, orders=orders, reports=reports, categories=categories)

# Маршрут для удаления товара
@app.route('/admin/delete_product/<int:product_id>')
def delete_product(product_id):
    if 'user_id' not in session or session.get('role_id') != 1:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            # Проверяем, нет ли товара в заказах, чтобы не нарушить целостность (опционально)
            cursor.execute("UPDATE Товары SET Статус_активности = 0 WHERE ID_Товара = %s", (product_id,))
        conn.close()
        flash("Товар успешно удален", "success")
    return redirect(url_for('admin_dashboard'))

# Маршрут для редактирования товара
@app.route('/admin/edit_product', methods=['POST'])
def edit_product():
    if 'user_id' not in session or session.get('role_id') != 1:
        return redirect(url_for('login'))

    # Получаем текстовые данные из формы
    p_id = request.form.get('id')
    name = request.form.get('name')
    price = request.form.get('price')
    cat_id = request.form.get('category_id')
    desc = request.form.get('description')

    # Проверка цены на отрицательное значение
    try:
        price_float = float(price)
        if price_float < 0:
            flash("Цена не может быть отрицательной", "error")
            return redirect(url_for('admin_dashboard'))
    except ValueError:
        flash("Неверный формат цены", "error")
        return redirect(url_for('admin_dashboard'))

    # Получаем файл изображения
    file = request.files.get('image')
    print(f"DEBUG: Получен файл: {file}")
    if file:
        print(f"DEBUG: Имя файла: {file.filename}")

    filename_to_save = None

    # Если файл был загружен и у него разрешенное расширение
    if file and file.filename != '' and allowed_file(file.filename):
        # Безопасное имя файла (защита от хакеров)
        filename = secure_filename(file.filename)
        # Генерируем уникальное имя, чтобы не затереть старые (добавляем ID товара)
        filename_to_save = f"product_{p_id}_{filename}"
        # Сохраняем файл на диск
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename_to_save))

    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            # Формируем SQL запрос динамически
            sql = "UPDATE Товары SET Название = %s, Цена = %s, ID_Категории = %s, Описание = %s"
            params = [name, price, cat_id, desc]

            # Если было загружено НОВОЕ изображение, добавляем его в запрос
            if filename_to_save:
                sql += ", Изображение = %s"
                params.append(filename_to_save)

            # Завершаем запрос условием WHERE
            sql += " WHERE ID_Товара = %s"
            params.append(p_id)

            cursor.execute(sql, tuple(params))
        conn.close()
        flash("Данные товара обновлены", "success")

    return redirect(url_for('admin_dashboard'))

# --- ДОБАВЛЕНИЕ ТОВАРА (АДМИН) ---
@app.route('/add_product', methods=['POST'])
def add_product():
    if 'role_id' not in session or session['role_id'] != 1:
        return redirect(url_for('login'))

    name = request.form.get('name')
    price = request.form.get('price')
    cat_id = request.form.get('category')
    desc = request.form.get('description')

    # Проверка цены на отрицательное значение
    try:
        price_float = float(price)
        if price_float < 0:
            flash("Цена не может быть отрицательной", "error")
            return redirect(url_for('admin_dashboard'))
    except ValueError:
        flash("Неверный формат цены", "error")
        return redirect(url_for('admin_dashboard'))
    
    # Обработка изображения
    file = request.files.get('image')
    filename_to_save = "default.jpg"

    if file and file.filename != '' and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Генерируем уникальное имя
        filename_to_save = f"new_{uuid.uuid4().hex}_{filename}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename_to_save))


    conn = get_db_connection()
    if conn:
        with conn.cursor() as cursor:
            sql = "INSERT INTO Товары (Название, Цена, ID_Категории, Описание, Изображение) VALUES (%s, %s, %s, %s, %s)"
            cursor.execute(sql, (name, price, cat_id, desc, filename_to_save))
        conn.close()
        flash("Товар успешно добавлен!", "success")
    
    return redirect(url_for('admin_dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
