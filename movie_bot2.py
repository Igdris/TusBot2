import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = '8032006876:AAE4b7z902XbYYQQ8VIW2J7kmIHTu8zVkO8'  # Замените на ваш токен
DB_NAME = 'movies_v2.db'
# ==================================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ========== КЛАСС ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ ==========
class MovieDatabase:
    """Класс для работы с базой данных фильмов"""
    
    def __init__(self, db_name: str = DB_NAME):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
        logger.info(f"База данных {db_name} подключена")
    
    def create_tables(self):
        """Создание таблиц если они не существуют"""
        cursor = self.conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                language_code TEXT DEFAULT 'ru',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица фильмов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                genre TEXT,
                year INTEGER,
                rating INTEGER CHECK(rating >= 0 AND rating <= 10),
                status TEXT DEFAULT 'want_to_watch',
                is_public BOOLEAN DEFAULT 1,
                priority INTEGER DEFAULT 3 CHECK(priority >= 1 AND priority <= 5),
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                watched_date TIMESTAMP,
                notes TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                UNIQUE(user_id, title)
            )
        ''')
        
        # Индексы для быстрого поиска
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_user_id ON movies(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_status ON movies(status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_is_public ON movies(is_public)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_genre ON movies(genre)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_movies_priority ON movies(priority)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity)')
        
        self.conn.commit()
    
    def add_or_update_user(self, user_id: int, username: str = None, first_name: str = None, language_code: str = 'ru'):
        """Добавление или обновление пользователя"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, first_name, language_code, last_activity) 
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (user_id, username or '', first_name or '', language_code))
        self.conn.commit()
    
    def update_user_activity(self, user_id: int):
        """Обновление времени последней активности"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE users SET last_activity = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        ''', (user_id,))
        self.conn.commit()
    
    def add_movie(self, user_id: int, title: str, genre: str = None, year: int = None, 
                  is_public: bool = True, priority: int = 3, notes: str = None) -> Optional[int]:
        """Добавление нового фильма"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO movies 
                (user_id, title, genre, year, is_public, priority, notes) 
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, title.strip(), genre, year, 1 if is_public else 0, priority, notes))
            self.conn.commit()
            
            if cursor.rowcount > 0:
                movie_id = cursor.lastrowid
                logger.info(f"Добавлен фильм: ID={movie_id}, user={user_id}, title='{title}'")
                return movie_id
            else:
                return None
        except Exception as e:
            logger.error(f"Ошибка при добавлении фильма: {e}")
            return None
    
    def get_user_movies(self, user_id: int, status: str = None, genre: str = None, 
                        year: int = None, priority: int = None, include_private: bool = True, 
                        limit: int = None) -> List[Dict]:
        """Получение фильмов пользователя с фильтрацией"""
        try:
            cursor = self.conn.cursor()
            
            query = '''
                SELECT id, title, status, added_date, is_public, genre, year, priority, notes, rating
                FROM movies 
                WHERE user_id = ?
            '''
            params = [user_id]
            
            if status:
                query += ' AND status = ?'
                params.append(status)
            
            if genre:
                query += ' AND genre LIKE ?'
                params.append(f'%{genre}%')
            
            if year:
                query += ' AND year = ?'
                params.append(year)
            
            if priority:
                query += ' AND priority = ?'
                params.append(priority)
            
            if not include_private:
                query += ' AND is_public = 1'
            
            query += ' ORDER BY priority ASC, added_date DESC'
            
            if limit:
                query += ' LIMIT ?'
                params.append(limit)
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка при получении фильмов пользователя: {e}")
            return []
    
    def get_movie_by_id(self, user_id: int, movie_id: int) -> Optional[Dict]:
        """Получение фильма по ID"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, title, status, is_public, genre, year, priority, notes, rating
                FROM movies 
                WHERE id = ? AND user_id = ?
            ''', (movie_id, user_id))
            
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка при получении фильма по ID: {e}")
            return None
    
    def update_movie(self, user_id: int, movie_id: int, **kwargs) -> bool:
        """Обновление информации о фильме"""
        try:
            if not kwargs:
                return False
            
            cursor = self.conn.cursor()
            
            # Формируем SET часть запроса
            set_clause = ', '.join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values())
            values.extend([movie_id, user_id])
            
            query = f'''
                UPDATE movies 
                SET {set_clause}
                WHERE id = ? AND user_id = ?
            '''
            
            cursor.execute(query, values)
            self.conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                logger.info(f"Фильм {movie_id} обновлен: {kwargs}")
            
            return success
        except Exception as e:
            logger.error(f"Ошибка при обновлении фильма: {e}")
            return False
    
    def mark_as_watched(self, user_id: int, movie_id: int, rating: int = None) -> bool:
        """Отметка фильма как просмотренного"""
        try:
            cursor = self.conn.cursor()
            
            update_data = {
                'status': 'watched',
                'watched_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            if rating is not None:
                update_data['rating'] = rating
            
            return self.update_movie(user_id, movie_id, **update_data)
        except Exception as e:
            logger.error(f"Ошибка при отметке фильма как просмотренного: {e}")
            return False
    
    def delete_movie(self, user_id: int, movie_id: int) -> bool:
        """Удаление фильма"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM movies WHERE id = ? AND user_id = ?', (movie_id, user_id))
            self.conn.commit()
            
            success = cursor.rowcount > 0
            if success:
                logger.info(f"Фильм {movie_id} удален")
            return success
        except Exception as e:
            logger.error(f"Ошибка при удалении фильма: {e}")
            return False
    
    def toggle_movie_privacy(self, user_id: int, movie_id: int) -> Optional[bool]:
        """Переключение приватности фильма"""
        try:
            cursor = self.conn.cursor()
            
            # Получаем текущее состояние
            cursor.execute('SELECT is_public FROM movies WHERE id = ? AND user_id = ?', (movie_id, user_id))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            current_state = bool(row[0])
            new_state = not current_state
            
            # Обновляем состояние
            cursor.execute('UPDATE movies SET is_public = ? WHERE id = ? AND user_id = ?', 
                         (1 if new_state else 0, movie_id, user_id))
            self.conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Приватность фильма {movie_id} изменена на {'публичный' if new_state else 'приватный'}")
                return new_state
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при изменении приватности фильма: {e}")
            return None
    
    def get_public_movies(self, limit: int = 100, genre: str = None, year: int = None) -> List[Dict]:
        """Получение всех публичных фильмов с фильтрацией"""
        try:
            cursor = self.conn.cursor()
            
            query = '''
                SELECT m.id, m.title, m.status, m.added_date, m.genre, m.year, m.rating,
                       u.user_id, u.username, u.first_name 
                FROM movies m
                LEFT JOIN users u ON m.user_id = u.user_id
                WHERE m.is_public = 1
            '''
            params = []
            
            if genre:
                query += ' AND m.genre LIKE ?'
                params.append(f'%{genre}%')
            
            if year:
                query += ' AND m.year = ?'
                params.append(year)
            
            query += ' ORDER BY m.added_date DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка при получении публичных фильмов: {e}")
            return []
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute('''
                SELECT 
                    COUNT(CASE WHEN status = 'want_to_watch' THEN 1 END) as want_count,
                    COUNT(CASE WHEN status = 'watched' THEN 1 END) as watched_count,
                    COUNT(CASE WHEN is_public = 1 THEN 1 END) as public_count,
                    AVG(CASE WHEN status = 'watched' AND rating IS NOT NULL THEN rating END) as avg_rating,
                    COUNT(CASE WHEN status = 'watched' AND rating IS NOT NULL THEN 1 END) as rated_count
                FROM movies 
                WHERE user_id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            result = dict(row) if row else {
                'want_count': 0, 'watched_count': 0, 'public_count': 0,
                'avg_rating': 0, 'rated_count': 0
            }
            
            # Округляем средний рейтинг
            if result['avg_rating']:
                result['avg_rating'] = round(result['avg_rating'], 1)
            
            return result
        except Exception as e:
            logger.error(f"Ошибка при получении статистики пользователя: {e}")
            return {'want_count': 0, 'watched_count': 0, 'public_count': 0, 'avg_rating': 0, 'rated_count': 0}
    
    def get_global_stats(self) -> Dict:
        """Получение глобальной статистики"""
        try:
            cursor = self.conn.cursor()
            
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_movies,
                    COUNT(DISTINCT user_id) as total_users,
                    COUNT(CASE WHEN status = 'want_to_watch' THEN 1 END) as total_want,
                    COUNT(CASE WHEN status = 'watched' THEN 1 END) as total_watched,
                    AVG(CASE WHEN status = 'watched' AND rating IS NOT NULL THEN rating END) as global_avg_rating
                FROM movies 
                WHERE is_public = 1
            ''')
            
            row = cursor.fetchone()
            result = dict(row) if row else {
                'total_movies': 0, 'total_users': 0, 'total_want': 0, 
                'total_watched': 0, 'global_avg_rating': 0
            }
            
            if result['global_avg_rating']:
                result['global_avg_rating'] = round(result['global_avg_rating'], 1)
            
            return result
        except Exception as e:
            logger.error(f"Ошибка при получении глобальной статистики: {e}")
            return {'total_movies': 0, 'total_users': 0, 'total_want': 0, 'total_watched': 0, 'global_avg_rating': 0}
    
    def get_top_genres(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Получение самых популярных жанров"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT genre, COUNT(*) as movie_count
                FROM movies
                WHERE is_public = 1 AND genre IS NOT NULL AND genre != ''
                GROUP BY genre
                ORDER BY movie_count DESC
                LIMIT ?
            ''', (limit,))
            
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка при получении топ жанров: {e}")
            return []
    
    def get_user_genres(self, user_id: int) -> List[Tuple[str, int]]:
        """Получение жанров пользователя"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT genre, COUNT(*) as count
                FROM movies
                WHERE user_id = ? AND genre IS NOT NULL AND genre != ''
                GROUP BY genre
                ORDER BY count DESC
            ''', (user_id,))
            
            return [(row[0], row[1]) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка при получении жанров пользователя: {e}")
            return []
    
    def get_random_movie(self, user_id: int, status: str = 'want_to_watch') -> Optional[Dict]:
        """Получение случайного фильма"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, title, genre, priority
                FROM movies
                WHERE user_id = ? AND status = ?
                ORDER BY RANDOM()
                LIMIT 1
            ''', (user_id, status))
            
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка при получении случайного фильма: {e}")
            return None
    
    def search_movies(self, user_id: int, query: str, search_in_public: bool = False) -> List[Dict]:
        """Поиск фильмов по названию"""
        try:
            cursor = self.conn.cursor()
            
            if search_in_public:
                cursor.execute('''
                    SELECT m.id, m.title, m.status, m.genre, m.year, m.rating,
                           u.first_name, u.username
                    FROM movies m
                    LEFT JOIN users u ON m.user_id = u.user_id
                    WHERE m.is_public = 1 AND m.title LIKE ?
                    ORDER BY m.added_date DESC
                    LIMIT 20
                ''', (f'%{query}%',))
            else:
                cursor.execute('''
                    SELECT id, title, status, genre, year, rating
                    FROM movies
                    WHERE user_id = ? AND title LIKE ?
                    ORDER BY added_date DESC
                    LIMIT 20
                ''', (user_id, f'%{query}%'))
            
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Ошибка при поиске фильмов: {e}")
            return []


# Инициализация базы данных
db = MovieDatabase()


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def format_movie_list(movies: List[Dict], show_status: bool = True, 
                      show_privacy: bool = False, show_priority: bool = False) -> str:
    """Форматирование списка фильмов"""
    if not movies:
        return "Список пуст."
    
    text = ""
    for i, movie in enumerate(movies[:50], 1):
        line = f"{i}. "
        
        if show_priority and movie.get('priority'):
            line += f"⭐" * movie['priority'] + " "
        
        if show_privacy:
            line += "👁️ " if movie.get('is_public', True) else "🔒 "
        
        line += movie['title']
        
        if movie.get('genre'):
            line += f" ({movie['genre']})"
        
        if movie.get('year'):
            line += f" [{movie['year']}]"
        
        if show_status and movie.get('status') == 'watched':
            line += " ✅"
            
            if movie.get('rating'):
                line += f" ⭐{movie['rating']}/10"
        
        text += line + "\n"
    
    if len(movies) > 50:
        text += f"\n... и еще {len(movies) - 50} фильмов"
    
    return text


def create_movie_keyboard(movie_id: int, include_back_button: bool = True) -> InlineKeyboardMarkup:
    """Создание клавиатуры для управления фильмом"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Просмотрен", callback_data=f"watch_{movie_id}"),
            InlineKeyboardButton("🔒 Приватность", callback_data=f"private_{movie_id}")
        ],
        [
            InlineKeyboardButton("⭐ Приоритет", callback_data=f"priority_{movie_id}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{movie_id}")
        ]
    ]
    
    if include_back_button:
        keyboard.append([InlineKeyboardButton("📋 Вернуться к списку", callback_data="my_movies")])
    
    return InlineKeyboardMarkup(keyboard)


def create_priority_keyboard(movie_id: int) -> InlineKeyboardMarkup:
    """Создание клавиатуры для выбора приоритета"""
    keyboard = [
        [
            InlineKeyboardButton("⭐ 1", callback_data=f"priority_{movie_id}_1"),
            InlineKeyboardButton("⭐⭐ 2", callback_data=f"priority_{movie_id}_2"),
            InlineKeyboardButton("⭐⭐⭐ 3", callback_data=f"priority_{movie_id}_3")
        ],
        [
            InlineKeyboardButton("⭐⭐⭐⭐ 4", callback_data=f"priority_{movie_id}_4"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐ 5", callback_data=f"priority_{movie_id}_5")
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data=f"movie_back_{movie_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def create_rating_keyboard(movie_id: int) -> InlineKeyboardMarkup:
    """Создание клавиатуры для оценки фильма"""
    keyboard = [
        [
            InlineKeyboardButton("1 ⭐", callback_data=f"rate_{movie_id}_1"),
            InlineKeyboardButton("2 ⭐", callback_data=f"rate_{movie_id}_2"),
            InlineKeyboardButton("3 ⭐", callback_data=f"rate_{movie_id}_3"),
            InlineKeyboardButton("4 ⭐", callback_data=f"rate_{movie_id}_4"),
            InlineKeyboardButton("5 ⭐", callback_data=f"rate_{movie_id}_5")
        ],
        [
            InlineKeyboardButton("6 ⭐", callback_data=f"rate_{movie_id}_6"),
            InlineKeyboardButton("7 ⭐", callback_data=f"rate_{movie_id}_7"),
            InlineKeyboardButton("8 ⭐", callback_data=f"rate_{movie_id}_8"),
            InlineKeyboardButton("9 ⭐", callback_data=f"rate_{movie_id}_9"),
            InlineKeyboardButton("10 ⭐", callback_data=f"rate_{movie_id}_10")
        ],
        [
            InlineKeyboardButton("Без оценки", callback_data=f"rate_{movie_id}_0"),
            InlineKeyboardButton("🔙 Отмена", callback_data=f"movie_back_{movie_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def create_main_keyboard() -> InlineKeyboardMarkup:
    """Создание основной клавиатуры"""
    keyboard = [
        [
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("🎲 Случайный", callback_data="random_movie")
        ],
        [
            InlineKeyboardButton("✅ Просмотренные", callback_data="watched"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_movies")
        ],
        [
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("❓ Помощь", callback_data="help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ========== ОСНОВНЫЕ КОМАНДЫ БОТА ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Регистрация/обновление пользователя
    db.add_or_update_user(user.id, user.username, user.first_name, user.language_code)
    db.update_user_activity(user.id)
    
    welcome_text = f"""
🎬 Добро пожаловать, {user.first_name}!

Я - бот для управления вашим списком фильмов.

📌 **Основные возможности:**
• 📋 Управление списком фильмов "Хочу посмотреть"
• ✅ Отметка просмотренных фильмов с оценкой
• ⭐ Система приоритетов (1-5 звезд)
• 🎲 Выбор случайного фильма для просмотра
• 🔍 Поиск фильмов по названию
• 👁️ Управление приватностью
• 📊 Подробная статистика
• 🏷️ Жанры и годы выпуска

📝 **Как использовать:**
1. Отправьте название фильма или используйте /add
2. Укажите жанр и год через запятую: "Инцепция, фантастика, 2010"
3. Используйте кнопки для управления
"""
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=create_main_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
📚 **Справка по командам:**

**Основные команды:**
/start - Начать работу с ботом
/add - Добавить фильм
/search - Поиск фильмов
/my - Мои фильмы
/watched - Просмотренные фильмы
/random - Случайный фильм из списка
/stats - Статистика
/help - Эта справка

**Формат добавления фильма:**
"Название фильма"
"Название, жанр"
"Название, жанр, год"

**Примеры:**
Интерстеллар
Интерстеллар, фантастика
Интерстеллар, фантастика, 2014

**Управление фильмами:**
⭐ Приоритет - важность фильма (1-5)
🔒 Приватность - скрыть/показать другим
✅ Просмотрен - отметить с оценкой
🗑️ Удалить - удалить из списка
"""
    
    await update.message.reply_text(
        help_text,
        reply_markup=create_main_keyboard()
    )


async def add_movie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add и текстовых сообщений"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    # Получение текста
    if context.args:
        text = ' '.join(context.args)
    elif update.message.text and not update.message.text.startswith('/'):
        text = update.message.text
    else:
        await update.message.reply_text(
            "📝 Отправьте название фильма для добавления.\n\n"
            "Можно указать жанр и год через запятую:\n"
            "• Инцепция\n"
            "• Инцепция, фантастика\n"
            "• Инцепция, фантастика, 2010",
            reply_markup=create_main_keyboard()
        )
        return
    
    # Парсинг входных данных
    parts = [part.strip() for part in text.split(',')]
    title = parts[0]
    genre = parts[1] if len(parts) > 1 else None
    year = None
    
    if len(parts) > 2:
        try:
            year = int(parts[2])
        except ValueError:
            year = None
    
    if not title or len(title) < 2:
        await update.message.reply_text(
            "❌ Название фильма слишком короткое.",
            reply_markup=create_main_keyboard()
        )
        return
    
    # Добавление фильма
    movie_id = db.add_movie(user.id, title, genre, year)
    
    if movie_id:
        # Получаем информацию о фильме для подтверждения
        movie_info = db.get_movie_by_id(user.id, movie_id)
        
        response_text = (
            f"✅ Фильм добавлен!\n\n"
            f"🎬 **{movie_info['title']}**\n"
        )
        
        if movie_info.get('genre'):
            response_text += f"🏷️ Жанр: {movie_info['genre']}\n"
        
        if movie_info.get('year'):
            response_text += f"📅 Год: {movie_info['year']}\n"
        
        response_text += (
            f"📊 Статус: Хочу посмотреть\n"
            f"⭐ Приоритет: {'⭐' * movie_info.get('priority', 3)}\n"
            f"👁️ Видимость: {'Публичный' if movie_info['is_public'] else 'Приватный'}\n\n"
            f"Используйте кнопки ниже для управления фильмом."
        )
        
        await update.message.reply_text(
            response_text,
            reply_markup=create_movie_keyboard(movie_id)
        )
    else:
        await update.message.reply_text(
            "❌ Этот фильм уже есть в вашем списке!",
            reply_markup=create_main_keyboard()
        )


async def show_my_movies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все фильмы пользователя"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    # Получаем фильмы с фильтрацией по аргументам
    status_filter = None
    genre_filter = None
    year_filter = None
    
    if context.args:
        for arg in context.args:
            arg_lower = arg.lower()
            if arg_lower in ['want', 'хочу', 'хочу посмотреть']:
                status_filter = 'want_to_watch'
            elif arg_lower in ['watched', 'просмотрено', 'просмотренные']:
                status_filter = 'watched'
            elif arg.isdigit() and len(arg) == 4:
                year_filter = int(arg)
            else:
                genre_filter = arg
    
    want_movies = db.get_user_movies(user.id, status='want_to_watch', genre=genre_filter, year=year_filter)
    watched_movies = db.get_user_movies(user.id, status='watched', genre=genre_filter, year=year_filter)
    
    # Получаем статистику
    stats = db.get_user_stats(user.id)
    
    # Формируем ответ
    text = f"🎬 **Ваши фильмы**\n\n"
    
    if genre_filter:
        text += f"🏷️ Фильтр: {genre_filter}\n"
    if year_filter:
        text += f"📅 Фильтр: {year_filter} год\n"
    
    text += f"📝 **Хочу посмотреть ({len(want_movies)}):**\n"
    text += format_movie_list(want_movies[:10], show_status=False, show_privacy=True, show_priority=True)
    
    text += f"\n✅ **Просмотрено ({len(watched_movies)}):**\n"
    text += format_movie_list(watched_movies[:10], show_status=True, show_privacy=True)
    
    if len(want_movies) > 10 or len(watched_movies) > 10:
        text += f"\n📄 Для просмотра всех фильмов используйте поиск или кнопки управления."
    
    text += f"\n📊 **Статистика:**\n"
    text += f"• Всего: {stats['want_count'] + stats['watched_count']}\n"
    text += f"• Хочу посмотреть: {stats['want_count']}\n"
    text += f"• Просмотрено: {stats['watched_count']}\n"
    
    if stats['rated_count'] > 0:
        text += f"• Средняя оценка: {stats['avg_rating']}/10"
    
    # Создаем клавиатуру
    keyboard = [
        [
            InlineKeyboardButton("🎲 Случайный", callback_data="random_movie"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_movies")
        ]
    ]
    
    # Добавляем жанры пользователя
    user_genres = db.get_user_genres(user.id)
    if user_genres:
        keyboard.append([InlineKeyboardButton("🏷️ Мои жанры", callback_data="my_genres")])
    
    keyboard.extend([
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ],
        [InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")]
    ])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_watched_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать просмотренные фильмы"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    watched_movies = db.get_user_movies(user.id, status='watched')
    stats = db.get_user_stats(user.id)
    
    text = f"✅ **Просмотренные фильмы ({stats['watched_count']})**\n\n"
    
    if watched_movies:
        # Сортируем по рейтингу или дате просмотра
        watched_movies_sorted = sorted(
            watched_movies, 
            key=lambda x: (x.get('rating') or 0, x.get('added_date') or ''), 
            reverse=True
        )
        
        text += format_movie_list(watched_movies_sorted[:15], show_status=False, show_privacy=True)
        
        if stats['rated_count'] > 0:
            text += f"\n⭐ **Средняя ваша оценка:** {stats['avg_rating']}/10"
    else:
        text += "У вас еще нет просмотренных фильмов.\nДобавьте фильмы и отметьте их как просмотренные!"
    
    # Создаем клавиатуру
    keyboard = []
    
    if stats['rated_count'] > 0:
        keyboard.append([
            InlineKeyboardButton("🏆 Топ по оценкам", callback_data="top_rated")
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies"),
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie")
        ]
    ])
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_public_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать публичный список фильмов"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    # Фильтрация по аргументам
    genre_filter = None
    year_filter = None
    
    if context.args:
        for arg in context.args:
            if arg.isdigit() and len(arg) == 4:
                year_filter = int(arg)
            else:
                genre_filter = arg
    
    public_movies = db.get_public_movies(limit=30, genre=genre_filter, year=year_filter)
    global_stats = db.get_global_stats()
    top_genres = db.get_top_genres(limit=5)
    
    text = "👁️ **Публичный список фильмов**\n\n"
    
    if genre_filter:
        text += f"🏷️ Фильтр по жанру: {genre_filter}\n"
    if year_filter:
        text += f"📅 Фильтр по году: {year_filter}\n"
    
    text += "Здесь отображаются публичные фильмы пользователей.\n\n"
    
    if public_movies:
        # Группируем фильмы по пользователям
        user_movies = {}
        for movie in public_movies:
            user_name = movie['first_name'] or f"User_{movie['user_id']}"
            if user_name not in user_movies:
                user_movies[user_name] = {'want': [], 'watched': []}
            
            movie_desc = movie['title']
            if movie.get('genre'):
                movie_desc += f" ({movie['genre']})"
            if movie.get('year'):
                movie_desc += f" [{movie['year']}]"
            
            if movie['status'] == 'watched':
                user_movies[user_name]['watched'].append(movie_desc)
            else:
                user_movies[user_name]['want'].append(movie_desc)
        
        # Формируем список
        for user_name, movies in list(user_movies.items())[:10]:
            total = len(movies['want']) + len(movies['watched'])
            if total > 0:
                text += f"👤 **{user_name}** (всего: {total})\n"
                
                if movies['want']:
                    text += f"  📝 Хочет: {len(movies['want'])}\n"
                
                if movies['watched']:
                    text += f"  ✅ Просмотрено: {len(movies['watched'])}\n"
                
                text += "\n"
    else:
        text += "Пока нет публичных фильмов.\nБудьте первым - добавьте фильм!"
    
    text += f"\n📊 **Общая статистика:**\n"
    text += f"• Фильмов: {global_stats['total_movies']}\n"
    text += f"• Пользователей: {global_stats['total_users']}\n"
    
    if top_genres:
        text += f"\n🏷️ **Популярные жанры:**\n"
        for genre, count in top_genres[:5]:
            text += f"• {genre}: {count}\n"
    
    keyboard = [
        [
            InlineKeyboardButton("🏷️ Жанры", callback_data="show_genres"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_public")
        ],
        [
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("➕ Добавить", callback_data="add_movie")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск фильмов"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    if not context.args:
        await update.message.reply_text(
            "🔍 **Поиск фильмов**\n\n"
            "Использование:\n"
            "/search <запрос> - поиск в вашем списке\n"
            "/search_public <запрос> - поиск в публичном списке\n\n"
            "Примеры:\n"
            "/search матрица\n"
            "/search_public криминальное чтиво",
            reply_markup=create_main_keyboard()
        )
        return
    
    query = ' '.join(context.args)
    movies = db.search_movies(user.id, query, search_in_public=False)
    
    text = f"🔍 **Результаты поиска по запросу: \"{query}\"**\n\n"
    
    if movies:
        text += format_movie_list(movies, show_status=True, show_privacy=True)
    else:
        text += "Ничего не найдено.\nПопробуйте другой запрос."
    
    keyboard = [
        [
            InlineKeyboardButton("🔍 Поиск в публичном", callback_data="search_public_menu"),
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def search_public_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск в публичном списке"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    if not context.args:
        await update.message.reply_text(
            "🔍 **Поиск в публичном списке**\n\n"
            "Напишите запрос для поиска фильмов среди публичных списков всех пользователей.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="public_list")]])
        )
        return
    
    query = ' '.join(context.args)
    movies = db.search_movies(user.id, query, search_in_public=True)
    
    text = f"🔍 **Результаты поиска в публичном списке: \"{query}\"**\n\n"
    
    if movies:
        for movie in movies[:15]:
            user_name = movie.get('first_name') or f"User_{movie.get('user_id')}"
            status_icon = "✅" if movie['status'] == 'watched' else "📝"
            text += f"{status_icon} {movie['title']}"
            
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            
            if movie.get('year'):
                text += f" [{movie['year']}]"
            
            text += f" — {user_name}\n"
    else:
        text += "Ничего не найдено."
    
    keyboard = [[InlineKeyboardButton("🔙 К публичному списку", callback_data="public_list")]]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def random_movie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор случайного фильма"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    movie = db.get_random_movie(user.id, 'want_to_watch')
    
    if movie:
        text = f"🎲 **Случайный фильм для просмотра:**\n\n"
        text += f"🎬 **{movie['title']}**\n"
        
        if movie.get('genre'):
            text += f"🏷️ Жанр: {movie['genre']}\n"
        
        if movie.get('priority'):
            text += f"⭐ Приоритет: {'⭐' * movie['priority']}\n"
        
        text += "\nХотите посмотреть этот фильм?"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, смотрю!", callback_data=f"watch_{movie['id']}"),
                InlineKeyboardButton("🎲 Другой фильм", callback_data="random_movie")
            ],
            [InlineKeyboardButton("📋 К списку", callback_data="my_movies")]
        ]
    else:
        text = "У вас нет фильмов в списке «Хочу посмотреть».\nДобавьте фильмы с помощью команды /add"
        keyboard = [[InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie")]]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    user = update.effective_user
    db.update_user_activity(user.id)
    
    # Получаем статистику
    user_stats = db.get_user_stats(user.id)
    global_stats = db.get_global_stats()
    user_genres = db.get_user_genres(user.id)
    top_genres = db.get_top_genres(limit=5)
    
    text = "📊 **Статистика**\n\n"
    
    text += "👤 **Ваша статистика:**\n"
    text += f"• Всего фильмов: {user_stats['want_count'] + user_stats['watched_count']}\n"
    text += f"• Хочу посмотреть: {user_stats['want_count']}\n"
    text += f"• Просмотрено: {user_stats['watched_count']}\n"
    text += f"• Публичных: {user_stats['public_count']}\n"
    
    if user_stats['rated_count'] > 0:
        text += f"• Средняя оценка: {user_stats['avg_rating']}/10\n"
    
    if user_genres:
        text += f"\n🏷️ **Ваши любимые жанры:**\n"
        for genre, count in user_genres[:5]:
            text += f"• {genre}: {count}\n"
    
    text += f"\n🌍 **Глобальная статистика:**\n"
    text += f"• Публичных фильмов: {global_stats['total_movies']}\n"
    text += f"• Участников: {global_stats['total_users']}\n"
    
    if global_stats['global_avg_rating'] > 0:
        text += f"• Средняя оценка: {global_stats['global_avg_rating']}/10\n"
    
    if top_genres:
        text += f"\n🏷️ **Популярные жанры:**\n"
        for genre, count in top_genres:
            text += f"• {genre}: {count}\n"
    
    keyboard = [
        [
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")
        ],
        [
            InlineKeyboardButton("🏆 Топ фильмов", callback_data="top_rated"),
            InlineKeyboardButton("🎲 Случайный", callback_data="random_movie")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    db.update_user_activity(user.id)
    logger.info(f"Кнопка: {data}, пользователь: {user.id}")
    
    # Основное меню
    if data == "main_menu":
        await query.edit_message_text(
            "🎬 **Главное меню**\n\nВыберите действие:",
            reply_markup=create_main_keyboard()
        )
    
    # Мои фильмы
    elif data == "my_movies":
        await handle_my_movies(query, user.id)
    
    # Просмотренные
    elif data == "watched":
        await handle_watched(query, user.id)
    
    # Случайный фильм
    elif data == "random_movie":
        await random_movie_command(update, context)
    
    # Поиск
    elif data == "search_movies":
        await query.edit_message_text(
            "🔍 **Поиск в вашем списке**\n\n"
            "Отправьте сообщение с названием фильма для поиска.\n\n"
            "Пример: матрица",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="my_movies")]])
        )
    
    elif data == "search_public_menu":
        await query.edit_message_text(
            "🔍 **Поиск в публичном списке**\n\n"
            "Отправьте сообщение с названием фильма для поиска среди публичных фильмов всех пользователей.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="public_list")]])
        )
    
    elif data == "search_public":
        await query.edit_message_text(
            "🔍 **Поиск в публичном списке**\n\n"
            "Отправьте сообщение с названием фильма для поиска.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="public_list")]])
        )
    
    # Публичный список
    elif data == "public_list":
        await handle_public_list(query)
    
    # Статистика
    elif data == "stats":
        await handle_stats(query, user.id)
    
    # Добавить фильм
    elif data == "add_movie":
        await query.edit_message_text(
            "📝 **Добавление фильма**\n\n"
            "Отправьте название фильма.\n\n"
            "Можно указать жанр и год через запятую:\n"
            "• Инцепция\n"
            "• Инцепция, фантастика\n"
            "• Инцепция, фантастика, 2010",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 На главную", callback_data="main_menu")]])
        )
    
    # Помощь
    elif data == "help":
        await handle_help(query)
    
    # Жанры
    elif data == "show_genres":
        top_genres = db.get_top_genres(limit=10)
        
        text = "🏷️ **Популярные жанры:**\n\n"
        for genre, count in top_genres:
            text += f"• {genre}: {count} фильмов\n"
        
        keyboard = [
            [InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")],
            [InlineKeyboardButton("🔙 Назад", callback_data="public_list")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "my_genres":
        user_genres = db.get_user_genres(user.id)
        
        text = "🏷️ **Ваши жанры:**\n\n"
        if user_genres:
            for genre, count in user_genres:
                text += f"• {genre}: {count} фильмов\n"
            
            keyboard = []
            for genre, _ in user_genres[:6]:
                keyboard.append([InlineKeyboardButton(genre, callback_data=f"filter_genre_{genre}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="my_movies")])
        else:
            text += "У вас пока нет фильмов с указанными жанрами."
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="my_movies")]]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Фильтрация по жанру
    elif data.startswith("filter_genre_"):
        genre = data.replace("filter_genre_", "")
        want_movies = db.get_user_movies(user.id, status='want_to_watch', genre=genre)
        watched_movies = db.get_user_movies(user.id, status='watched', genre=genre)
        
        text = f"🏷️ **Фильмы в жанре: {genre}**\n\n"
        text += f"📝 **Хочу посмотреть ({len(want_movies)}):**\n"
        text += format_movie_list(want_movies[:10], show_status=False, show_priority=True)
        
        text += f"\n✅ **Просмотрено ({len(watched_movies)}):**\n"
        text += format_movie_list(watched_movies[:10], show_status=True)
        
        keyboard = [
            [InlineKeyboardButton("🔙 К жанрам", callback_data="my_genres")],
            [InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Топ по оценкам
    elif data == "top_rated":
        watched_movies = db.get_user_movies(user.id, status='watched')
        
        # Фильтруем фильмы с оценкой и сортируем
        rated_movies = [m for m in watched_movies if m.get('rating')]
        rated_movies_sorted = sorted(rated_movies, key=lambda x: x.get('rating', 0), reverse=True)
        
        text = "🏆 **Ваши лучшие фильмы:**\n\n"
        
        if rated_movies_sorted:
            for i, movie in enumerate(rated_movies_sorted[:10], 1):
                text += f"{i}. ⭐{movie['rating']}/10 - {movie['title']}\n"
                if movie.get('genre'):
                    text += f"   ({movie['genre']})\n"
        else:
            text += "У вас пока нет оцененных фильмов.\nОтмечайте фильмы как просмотренные и ставьте оценки!"
        
        keyboard = [
            [InlineKeyboardButton("✅ Просмотренные", callback_data="watched")],
            [InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # Управление фильмом
    elif data.startswith("watch_"):
        await handle_watch_button(query, user.id, data)
    
    elif data.startswith("private_"):
        await handle_private_button(query, user.id, data)
    
    elif data.startswith("delete_"):
        await handle_delete_button(query, user.id, data)
    
    elif data.startswith("priority_"):
        parts = data.split("_")
        movie_id = int(parts[1])
        
        if len(parts) == 3:
            # Выбор конкретного приоритета
            priority = int(parts[2])
            
            success = db.update_movie(user.id, movie_id, priority=priority)
            if success:
                movie = db.get_movie_by_id(user.id, movie_id)
                text = f"✅ Приоритет фильма \"{movie['title']}\" изменен на {'⭐' * priority}\n\n"
                text += "Что дальше?"
                
                await query.edit_message_text(
                    text,
                    reply_markup=create_movie_keyboard(movie_id)
                )
        else:
            # Меню выбора приоритета
            movie = db.get_movie_by_id(user.id, movie_id)
            
            if movie:
                text = f"⭐ **Установите приоритет для фильма:**\n\n"
                text += f"🎬 {movie['title']}\n"
                text += f"Текущий приоритет: {'⭐' * movie.get('priority', 3)}\n\n"
                text += "1 ⭐ - низкий приоритет\n"
                text += "5 ⭐ - высокий приоритет"
                
                await query.edit_message_text(
                    text,
                    reply_markup=create_priority_keyboard(movie_id)
                )
    
    elif data.startswith("rate_"):
        parts = data.split("_")
        movie_id = int(parts[1])
        rating = int(parts[2])
        
        if rating > 0:
            success = db.mark_as_watched(user.id, movie_id, rating=rating)
            if success:
                movie = db.get_movie_by_id(user.id, movie_id)
                text = f"✅ Фильм \"{movie['title']}\" отмечен как просмотренный с оценкой ⭐{rating}/10!\n\n"
                text += "Спасибо за оценку!"
            else:
                text = "❌ Не удалось поставить оценку."
        else:
            success = db.mark_as_watched(user.id, movie_id)
            if success:
                movie = db.get_movie_by_id(user.id, movie_id)
                text = f"✅ Фильм \"{movie['title']}\" отмечен как просмотренный без оценки.\n\n"
            else:
                text = "❌ Не удалось отметить фильм как просмотренный."
        
        keyboard = [
            [
                InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
                InlineKeyboardButton("✅ Просмотренные", callback_data="watched")
            ]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith("movie_back_"):
        movie_id = int(data.split("_")[2])
        movie = db.get_movie_by_id(user.id, movie_id)
        
        if movie:
            text = f"🎬 **{movie['title']}**\n\n"
            
            if movie.get('genre'):
                text += f"🏷️ Жанр: {movie['genre']}\n"
            
            if movie.get('year'):
                text += f"📅 Год: {movie['year']}\n"
            
            if movie.get('notes'):
                text += f"📝 Заметки: {movie['notes']}\n"
            
            text += f"📊 Статус: {'Просмотрен ✅' if movie['status'] == 'watched' else 'Хочу посмотреть'}\n"
            text += f"⭐ Приоритет: {'⭐' * movie.get('priority', 3)}\n"
            text += f"👁️ Видимость: {'Публичный' if movie['is_public'] else 'Приватный'}\n\n"
            text += "Используйте кнопки для управления:"
            
            await query.edit_message_text(
                text,
                reply_markup=create_movie_keyboard(movie_id)
            )
    
    else:
        # Если callback_data не распознан
        await query.edit_message_text(
            "❌ Неизвестная команда. Возвращаюсь в главное меню.",
            reply_markup=create_main_keyboard()
        )


async def handle_my_movies(query, user_id):
    """Обработка кнопки 'Мои фильмы'"""
    want_movies = db.get_user_movies(user_id, status='want_to_watch', limit=5)
    watched_movies = db.get_user_movies(user_id, status='watched', limit=3)
    stats = db.get_user_stats(user_id)
    
    text = f"🎬 **Ваши фильмы**\n\n"
    text += f"📝 Хочу посмотреть: {stats['want_count']} фильмов\n"
    text += f"✅ Просмотрено: {stats['watched_count']} фильмов\n\n"
    
    if want_movies:
        text += "🎬 **Последние добавленные:**\n"
        for movie in want_movies:
            text += f"• {movie['title']}"
            if movie.get('priority'):
                text += f" {'⭐' * movie['priority']}"
            text += "\n"
    
    text += "\nВыберите действие:"
    
    # Создаем клавиатуру
    keyboard = [
        [
            InlineKeyboardButton("🎲 Случайный фильм", callback_data="random_movie"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_movies")
        ]
    ]
    
    # Быстрые действия для фильмов
    if want_movies:
        keyboard.append([InlineKeyboardButton("🏷️ Мои жанры", callback_data="my_genres")])
    
    keyboard.extend([
        [
            InlineKeyboardButton("✅ Просмотренные", callback_data="watched"),
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")
        ],
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ]
    ])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_watched(query, user_id):
    """Обработка кнопки 'Просмотренные'"""
    watched_movies = db.get_user_movies(user_id, status='watched', limit=15)
    stats = db.get_user_stats(user_id)
    
    text = f"✅ **Просмотренные фильмы ({stats['watched_count']})**\n\n"
    
    if watched_movies:
        for i, movie in enumerate(watched_movies, 1):
            text += f"{i}. {movie['title']}"
            if movie.get('rating'):
                text += f" ⭐{movie['rating']}/10"
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            text += "\n"
        
        if stats['watched_count'] > 15:
            text += f"\n... и еще {stats['watched_count'] - 15} фильмов"
    else:
        text += "Пока нет просмотренных фильмов."
    
    # Создаем клавиатуру
    keyboard = []
    
    if stats['rated_count'] > 0:
        keyboard.append([InlineKeyboardButton("🏆 Топ по оценкам", callback_data="top_rated")])
    
    keyboard.extend([
        [
            InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies"),
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie")
        ]
    ])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_public_list(query):
    """Обработка кнопки 'Публичный список'"""
    public_movies = db.get_public_movies(limit=15)
    global_stats = db.get_global_stats()
    top_genres = db.get_top_genres(limit=3)
    
    text = "👁️ **Публичный список фильмов**\n\n"
    
    if public_movies:
        text += "🎬 **Последние добавленные:**\n"
        
        for i, movie in enumerate(public_movies[:10], 1):
            user_name = movie['first_name'] or f"User_{movie['user_id']}"
            status_icon = "✅" if movie['status'] == 'watched' else "📝"
            text += f"{i}. {status_icon} {movie['title']}"
            
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            
            if movie.get('year'):
                text += f" [{movie['year']}]"
            
            text += f" — {user_name}\n"
        
        if len(public_movies) > 10:
            text += f"\n... и еще {len(public_movies) - 10} фильмов\n"
    else:
        text += "Пока нет публичных фильмов.\n"
    
    text += f"\n📊 **Статистика:**\n"
    text += f"• Фильмов: {global_stats['total_movies']}\n"
    text += f"• Пользователей: {global_stats['total_users']}\n"
    
    if top_genres:
        text += f"\n🏷️ **Популярные жанры:**\n"
        for genre, count in top_genres:
            text += f"• {genre}: {count}\n"
    
    keyboard = [
        [
            InlineKeyboardButton("🏷️ Все жанры", callback_data="show_genres"),
            InlineKeyboardButton("🔍 Поиск", callback_data="search_public_menu")
        ],
        [
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("➕ Добавить", callback_data="add_movie")
        ]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_stats(query, user_id):
    """Обработка кнопки 'Статистика'"""
    user_stats = db.get_user_stats(user_id)
    global_stats = db.get_global_stats()
    
    text = "📊 **Статистика**\n\n"
    
    text += "👤 **Ваши данные:**\n"
    text += f"• Хочу посмотреть: {user_stats['want_count']}\n"
    text += f"• Просмотрено: {user_stats['watched_count']}\n"
    text += f"• Публичных: {user_stats['public_count']}\n\n"
    
    text += "🌍 **Общая статистика:**\n"
    text += f"• Фильмов: {global_stats['total_movies']}\n"
    text += f"• Участников: {global_stats['total_users']}"
    
    keyboard = [
        [
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")
        ]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_help(query):
    """Обработка кнопки 'Помощь'"""
    help_text = """
📚 **Управление фильмами:**

**Основные действия:**
• Напишите название фильма, чтобы добавить его
• Используйте кнопки под сообщениями для управления

**Расширенное добавление:**
"Название, жанр, год"
Пример: "Интерстеллар, фантастика, 2014"

**Кнопки управления фильмом:**
✅ Просмотрен - отметить с оценкой
🔒 Приватность - скрыть/показать фильм
⭐ Приоритет - установить важность (1-5)
🗑️ Удалить - удалить из списка

**Навигация:**
🎲 Случайный - случайный фильм из списка
🔍 Поиск - найти фильм по названию
🏷️ Жанры - фильтрация по жанрам
📊 Статистика - ваша и общая статистика
"""
    
    keyboard = [
        [InlineKeyboardButton("📋 На главную", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_watch_button(query, user_id, data):
    """Обработка кнопки 'Просмотрен'"""
    movie_id = int(data.split('_')[1])
    movie = db.get_movie_by_id(user_id, movie_id)
    
    if movie and movie['status'] == 'want_to_watch':
        # Показываем клавиатуру для оценки
        text = f"✅ **Отметить фильм как просмотренный:**\n\n"
        text += f"🎬 {movie['title']}\n\n"
        text += "Поставьте оценку (от 1 до 10):"
        
        await query.edit_message_text(
            text,
            reply_markup=create_rating_keyboard(movie_id)
        )
    else:
        await query.edit_message_text("❌ Этот фильм уже просмотрен или не найден.")


async def handle_private_button(query, user_id, data):
    """Обработка кнопки 'Приватность'"""
    movie_id = int(data.split('_')[1])
    new_state = db.toggle_movie_privacy(user_id, movie_id)
    
    if new_state is not None:
        movie = db.get_movie_by_id(user_id, movie_id)
        if movie:
            status_text = "публичным" if new_state else "приватным"
            icon = "👁️" if new_state else "🔒"
            
            text = f"✅ Фильм \"{movie['title']}\" теперь {status_text}!\n\nСтатус: {icon} {'Публичный' if new_state else 'Приватный'}"
            
            await query.edit_message_text(
                text,
                reply_markup=create_movie_keyboard(movie_id)
            )
        else:
            await query.edit_message_text("❌ Фильм не найден.")
    else:
        await query.edit_message_text("❌ Не удалось изменить приватность фильма.")


async def handle_delete_button(query, user_id, data):
    """Обработка кнопки 'Удалить'"""
    movie_id = int(data.split('_')[1])
    success = db.delete_movie(user_id, movie_id)
    
    if success:
        text = "🗑️ Фильм удален из вашего списка!"
        
        keyboard = [
            [
                InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
                InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie")
            ]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text("❌ Не удалось удалить фильм.")


# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def main():
    """Основная функция запуска бота"""
    print("=" * 50)
    print("🎬 Movie Bot Pro - Продвинутый бот для управления фильмами")
    print("=" * 50)
    print("Инициализация бота...")
    
    try:
        # Создаем Application
        application = Application.builder().token(TOKEN).build()
        
        # Регистрируем обработчики команд
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("add", add_movie_command))
        application.add_handler(CommandHandler("my", show_my_movies_command))
        application.add_handler(CommandHandler("watched", show_watched_command))
        application.add_handler(CommandHandler("public", show_public_list_command))
        application.add_handler(CommandHandler("search", search_command))
        application.add_handler(CommandHandler("search_public", search_public_command))
        application.add_handler(CommandHandler("random", random_movie_command))
        application.add_handler(CommandHandler("stats", show_stats_command))
        
        # Обработчик текстовых сообщений (для добавления фильмов)
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            add_movie_command
        ))
        
        # Обработчик кнопок
        application.add_handler(CallbackQueryHandler(button_handler))
        
        print("✅ Бот инициализирован успешно!")
        print("✅ База данных создана/подключена")
        print("=" * 50)
        print("🚀 Запускаю бота...")
        print("📡 Ожидаю сообщения...")
        print("=" * 50)
        print("Для остановки нажмите Ctrl+C")
        print("=" * 50)
        
        # Запускаем бота
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        print(f"\n❌ Ошибка: {e}")
        print("\nВозможные причины:")
        print("1. Неверный токен бота")
        print("2. Библиотека python-telegram-bot не установлена")
        print("3. Проблемы с интернет-соединением")
        print("\nРешение:")
        print("pip install python-telegram-bot==20.3")
        print("Убедитесь, что токен корректен")


# ========== ЗАПУСК ПРОГРАММЫ ==========
if __name__ == '__main__':
    main()