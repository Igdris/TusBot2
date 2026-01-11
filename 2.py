import logging
import sqlite3
import asyncio
import random
import string
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8032006876:AAE4b7z902XbYYQQ8VIW2J7kmIHTu8zVkO8"  # Ваш токен
MOVIES_DB_NAME = 'movies_v3.db'
WORDS_DB_NAME = "whoami_simple.db"
# ==================================

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ДЛЯ ФИЛЬМОВ ==========
class MovieDatabase:
    """Класс для работы с базой данных фильмов"""
    
    def __init__(self, db_name: str = MOVIES_DB_NAME):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()
        logger.info(f"База данных фильмов {db_name} подключена")
    
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
        
        # Таблица фильмов (упрощённая, без priority)
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
                  is_public: bool = True, notes: str = None) -> Optional[int]:
        """Добавление нового фильма"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO movies 
                (user_id, title, genre, year, is_public, notes) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, title.strip(), genre, year, 1 if is_public else 0, notes))
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
                        year: int = None, include_private: bool = True, 
                        limit: int = None) -> List[Dict]:
        """Получение фильмов пользователя с фильтрацией"""
        try:
            cursor = self.conn.cursor()
            
            query = '''
                SELECT id, title, status, added_date, is_public, genre, year, notes, rating
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
            
            if not include_private:
                query += ' AND is_public = 1'
            
            query += ' ORDER BY added_date DESC'
            
            if limit:
                query += ' LIMIT ?'
                params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка при получении фильмов пользователя: {e}")
            return []
    
    def get_movie_by_id(self, user_id: int, movie_id: int) -> Optional[Dict]:
        """Получение фильма по ID"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, title, status, is_public, genre, year, notes, rating
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
            
            return self.update_movie(user.id, movie_id, **update_data)
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
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
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
            if row:
                result = dict(row)
            else:
                result = {
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
            if row:
                result = dict(row)
            else:
                result = {
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
            
            rows = cursor.fetchall()
            return [(row[0], row[1]) for row in rows]
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
            
            rows = cursor.fetchall()
            return [(row[0], row[1]) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка при получении жанров пользователя: {e}")
            return []
    
    def get_random_movie(self, user_id: int, status: str = 'want_to_watch') -> Optional[Dict]:
        """Получение случайного фильма"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, title, genre
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
            
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка при поиске фильмов: {e}")
            return []

# ========== БАЗА ДАННЫХ ДЛЯ ИГРЫ "КТО Я?" ==========
class WordGameDatabase:
    def __init__(self, db_name: str = WORDS_DB_NAME):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        """Создание таблиц для игры 'Кто я?'"""
        cursor = self.conn.cursor()
        
        # Таблица игр
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                owner_id INTEGER,
                status TEXT DEFAULT 'created',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица игроков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                user_id INTEGER,
                user_name TEXT,
                word_for_others TEXT,
                guessed_correctly BOOLEAN DEFAULT FALSE,
                words_received INTEGER DEFAULT 0,
                last_word_received_at TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        ''')
        
        # Таблица слов игроков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                from_user_id INTEGER,
                to_user_id INTEGER,
                word TEXT,
                is_guessed BOOLEAN DEFAULT FALSE,
                guessed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        ''')
        
        # Таблица пар игроков
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS player_pairs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                from_user_id INTEGER,
                to_user_id INTEGER,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        ''')
        
        self.conn.commit()
    
    def create_game(self, game_id: str, owner_id: int):
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO games (game_id, owner_id) VALUES (?, ?)",
                (game_id, owner_id)
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка создания игры: {e}")
            return False
    
    def get_game(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))
        columns = [description[0] for description in cursor.description]
        row = cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None
    
    def update_game_status(self, game_id: str, status: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE games SET status = ? WHERE game_id = ?",
            (status, game_id)
        )
        self.conn.commit()
    
    def add_player(self, game_id: str, user_id: int, user_name: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id FROM players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id)
        )
        if cursor.fetchone():
            return False
        
        cursor.execute(
            "INSERT INTO players (game_id, user_id, user_name) VALUES (?, ?, ?)",
            (game_id, user_id, user_name)
        )
        self.conn.commit()
        return True
    
    def get_players(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM players WHERE game_id = ? ORDER BY id",
            (game_id,)
        )
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_player(self, game_id: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM players WHERE game_id = ? AND user_id = ?",
            (game_id, user_id)
        )
        columns = [description[0] for description in cursor.description]
        row = cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None
    
    def set_player_word_for_others(self, game_id: str, user_id: int, word: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE players SET word_for_others = ? WHERE game_id = ? AND user_id = ?",
            (word, game_id, user_id)
        )
        self.conn.commit()
    
    def add_player_pair(self, game_id: str, from_user_id: int, to_user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO player_pairs (game_id, from_user_id, to_user_id) VALUES (?, ?, ?)",
            (game_id, from_user_id, to_user_id)
        )
        self.conn.commit()
    
    def get_player_pair(self, game_id: str, from_user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM player_pairs WHERE game_id = ? AND from_user_id = ?",
            (game_id, from_user_id)
        )
        columns = [description[0] for description in cursor.description]
        row = cursor.fetchone()
        if row:
            return dict(zip(columns, row))
        return None
    
    def get_all_pairs(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM player_pairs WHERE game_id = ?",
            (game_id,)
        )
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def clear_player_pairs(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM player_pairs WHERE game_id = ?", (game_id,))
        self.conn.commit()
    
    def add_player_word(self, game_id: str, from_user_id: int, to_user_id: int, word: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO player_words (game_id, from_user_id, to_user_id, word) VALUES (?, ?, ?, ?)",
            (game_id, from_user_id, to_user_id, word)
        )
        self.conn.commit()
    
    def get_word_for_player(self, game_id: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, word, from_user_id, is_guessed FROM player_words WHERE game_id = ? AND to_user_id = ? AND is_guessed = FALSE ORDER BY id LIMIT 1",
            (game_id, user_id)
        )
        return cursor.fetchone()
    
    def get_all_unguessed_words_for_player(self, game_id: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, word, from_user_id, is_guessed FROM player_words WHERE game_id = ? AND to_user_id = ? AND is_guessed = FALSE",
            (game_id, user_id)
        )
        return cursor.fetchall()
    
    def mark_word_as_guessed(self, word_id: int, game_id: str, to_user_id: int):
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE player_words SET is_guessed = TRUE, guessed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (word_id,)
            )
            cursor.execute(
                "UPDATE players SET words_received = words_received + 1 WHERE game_id = ? AND user_id = ?",
                (game_id, to_user_id)
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка при отметке слова как угаданного: {e}")
            return False
    
    def get_all_player_words(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            """SELECT pw.*, p1.user_name as from_name, p2.user_name as to_name 
               FROM player_words pw
               LEFT JOIN players p1 ON pw.from_user_id = p1.user_id AND p1.game_id = pw.game_id
               LEFT JOIN players p2 ON pw.to_user_id = p2.user_id AND p2.game_id = pw.game_id
               WHERE pw.game_id = ?""",
            (game_id,)
        )
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_visible_words_for_player(self, game_id: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            """SELECT pw.*, p1.user_name as from_name, p2.user_name as to_name 
               FROM player_words pw
               LEFT JOIN players p1 ON pw.from_user_id = p1.user_id AND p1.game_id = pw.game_id
               LEFT JOIN players p2 ON pw.to_user_id = p2.user_id AND p2.game_id = pw.game_id
               WHERE pw.game_id = ? AND pw.to_user_id != ? AND pw.is_guessed = FALSE""",
            (game_id, user_id)
        )
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_player_words_count(self, game_id: str, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM player_words WHERE game_id = ? AND to_user_id = ? AND is_guessed = FALSE",
            (game_id, user_id)
        )
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def get_pairs_count(self, game_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM player_pairs WHERE game_id = ?",
            (game_id,)
        )
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def delete_game(self, game_id: str):
        cursor = self.conn.cursor()
        try:
            cursor.execute("DELETE FROM player_pairs WHERE game_id = ?", (game_id,))
            cursor.execute("DELETE FROM player_words WHERE game_id = ?", (game_id,))
            cursor.execute("DELETE FROM players WHERE game_id = ?", (game_id,))
            cursor.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Ошибка удаления игры: {e}")
            return False
    
    def get_all_games_for_user(self, user_id: int):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT g.* FROM games g
            JOIN players p ON g.game_id = p.game_id
            WHERE p.user_id = ? AND g.status IN ('created', 'collecting', 'started')
        ''', (user_id,))
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def get_players_to_guess_for(self, game_id: str, from_user_id: int):
        """Получить список игроков, которым текущий игрок может загадать слово"""
        cursor = self.conn.cursor()
        
        # Получаем всех игроков в игре, кроме себя
        cursor.execute(
            "SELECT user_id, user_name FROM players WHERE game_id = ? AND user_id != ? ORDER BY id",
            (game_id, from_user_id)
        )
        all_players = cursor.fetchall()
        
        # Получаем игроков, которым текущий игрок уже загадывал слова
        cursor.execute(
            "SELECT DISTINCT to_user_id FROM player_words WHERE game_id = ? AND from_user_id = ? AND is_guessed = FALSE",
            (game_id, from_user_id)
        )
        already_guessed_for = [row[0] for row in cursor.fetchall()]
        
        # Фильтруем игроков, которые уже имеют слово от текущего игрока
        available_players = []
        for player_id, player_name in all_players:
            if player_id not in already_guessed_for:
                available_players.append((player_id, player_name))
        
        return available_players
    
    def has_player_guessed_all_words(self, game_id: str, user_id: int):
        """Проверить, угадал ли игрок все свои слова"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM player_words WHERE game_id = ? AND to_user_id = ? AND is_guessed = FALSE",
            (game_id, user_id)
        )
        result = cursor.fetchone()
        unguessed_count = result[0] if result else 0
        return unguessed_count == 0

# Инициализация баз данных
movies_db = MovieDatabase()
word_game_db = WordGameDatabase()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def generate_game_code():
    """Генерация 4-символьного кода для игры"""
    return ''.join(random.choices(string.ascii_uppercase, k=4))

def create_main_keyboard():
    """Создание главной клавиатуры"""
    keyboard = [
        [KeyboardButton("🎬 Управление фильмами")],
        [KeyboardButton("🎮 Игра 'Кто я?'"), KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_game_lobby_keyboard(game_id: str, is_owner: bool = False):
    """Клавиатура для лобби игры"""
    keyboard = []
    
    keyboard.append([
        InlineKeyboardButton("🔗 Пригласить друга", callback_data=f"invite_{game_id}")
    ])
    
    if is_owner:
        keyboard.append([
            InlineKeyboardButton("▶️ Начать игру", callback_data=f"start_{game_id}")
        ])
        keyboard.append([
            InlineKeyboardButton("❌ Отменить игру", callback_data=f"cancel_{game_id}")
        ])
    
    return InlineKeyboardMarkup(keyboard)

def create_waiting_keyboard(game_id: str):
    """Клавиатура во время ожидания слов"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("👥 Игроки", callback_data=f"players_{game_id}")]])

def create_game_keyboard(game_id: str):
    """Клавиатура во время игры"""
    keyboard = [
        [InlineKeyboardButton("👥 Игроки", callback_data=f"players_{game_id}")],
        [InlineKeyboardButton("📝 Загадать слово", callback_data=f"giveword_{game_id}")],
        [InlineKeyboardButton("🎯 Угадать свое слово", callback_data=f"guess_{game_id}")],
        [InlineKeyboardButton("🔍 Показать слова", callback_data=f"showwords_{game_id}")],
        [InlineKeyboardButton("🏁 Завершить игру", callback_data=f"end_{game_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_guess_keyboard(game_id: str):
    """Клавиатура для угадывания слова"""
    keyboard = [
        [InlineKeyboardButton("🎯 Попробовать угадать", callback_data=f"tryguess_{game_id}")],
        [InlineKeyboardButton("📝 Загадать слово", callback_data=f"giveword_{game_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"back_{game_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_player_selection_keyboard(game_id: str, players):
    """Клавиатура для выбора игрока для загадывания слова"""
    keyboard = []
    
    for player_id, player_name in players:
        keyboard.append([
            InlineKeyboardButton(f"👤 {player_name}", callback_data=f"selectplayer_{game_id}_{player_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f"back_{game_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

# ========== ФУНКЦИИ ДЛЯ ФИЛЬМОВ ==========
def format_movie_list(movies: List[Dict], show_status: bool = True, 
                      show_privacy: bool = False) -> str:
    """Форматирование списка фильмов"""
    if not movies:
        return "Список пуст."
    
    text = ""
    for i, movie in enumerate(movies[:50], 1):
        line = f"{i}. "
        
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

def create_movie_widget(movie_id: int) -> InlineKeyboardMarkup:
    """Создание виджета управления фильмом"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Просмотрен", callback_data=f"watch_{movie_id}"),
            InlineKeyboardButton("🔒 Приватность", callback_data=f"private_{movie_id}")
        ],
        [
            InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{movie_id}")
        ],
        [
            InlineKeyboardButton("📋 К списку", callback_data="my_movies"),
            InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def create_rating_widget(movie_id: int) -> InlineKeyboardMarkup:
    """Создание виджета для оценки фильма"""
    keyboard = [
        [
            InlineKeyboardButton("⭐ 1", callback_data=f"rate_{movie_id}_1"),
            InlineKeyboardButton("⭐ 2", callback_data=f"rate_{movie_id}_2"),
            InlineKeyboardButton("⭐ 3", callback_data=f"rate_{movie_id}_3"),
            InlineKeyboardButton("⭐ 4", callback_data=f"rate_{movie_id}_4"),
            InlineKeyboardButton("⭐ 5", callback_data=f"rate_{movie_id}_5")
        ],
        [
            InlineKeyboardButton("⭐ 6", callback_data=f"rate_{movie_id}_6"),
            InlineKeyboardButton("⭐ 7", callback_data=f"rate_{movie_id}_7"),
            InlineKeyboardButton("⭐ 8", callback_data=f"rate_{movie_id}_8"),
            InlineKeyboardButton("⭐ 9", callback_data=f"rate_{movie_id}_9"),
            InlineKeyboardButton("⭐ 10", callback_data=f"rate_{movie_id}_10")
        ],
        [
            InlineKeyboardButton("👁️ Без оценки", callback_data=f"rate_{movie_id}_0"),
            InlineKeyboardButton("🔙 Отмена", callback_data=f"movie_back_{movie_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_main_widget() -> InlineKeyboardMarkup:
    """Создание главного виджета меню"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 Мои фильмы", callback_data="my_movies"),
            InlineKeyboardButton("✅ Просмотренные", callback_data="watched")
        ],
        [
            InlineKeyboardButton("🎲 Случайный фильм", callback_data="random_movie"),
            InlineKeyboardButton("🔍 Поиск фильмов", callback_data="search_movies")
        ],
        [
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("🗑️ Удалить фильм", callback_data="delete_movie")
        ],
        [
            InlineKeyboardButton("🎮 Игра 'Кто я?'", callback_data="word_game"),
            InlineKeyboardButton("🎮 Текущие игры", callback_data="current_games")
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_movies_widget() -> InlineKeyboardMarkup:
    """Создание виджета для раздела фильмов"""
    keyboard = [
        [
            InlineKeyboardButton("🎲 Случайный фильм", callback_data="random_movie"),
            InlineKeyboardButton("🔍 Поиск фильмов", callback_data="search_movies")
        ],
        [
            InlineKeyboardButton("🏷️ Мои жанры", callback_data="my_genres"),
            InlineKeyboardButton("✅ Просмотренные", callback_data="watched")
        ],
        [
            InlineKeyboardButton("👁️ Публичный список", callback_data="public_list"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("🗑️ Удалить фильм", callback_data="delete_movie")
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_back_widget(back_to: str = "main_menu") -> InlineKeyboardMarkup:
    """Создание виджета с кнопкой назад"""
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=back_to)]]
    return InlineKeyboardMarkup(keyboard)

def create_word_game_main_widget() -> InlineKeyboardMarkup:
    """Создание виджета для игры 'Кто я?'"""
    keyboard = [
        [
            InlineKeyboardButton("🎮 Новая игра", callback_data="new_word_game"),
            InlineKeyboardButton("📋 Мои игры", callback_data="my_word_games")
        ],
        [
            InlineKeyboardButton("🔗 Присоединиться", callback_data="join_word_game"),
            InlineKeyboardButton("❓ Правила", callback_data="word_game_rules")
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_delete_movie_widget() -> InlineKeyboardMarkup:
    """Создание виджета для удаления фильма"""
    keyboard = [
        [
            InlineKeyboardButton("🔙 Отмена", callback_data="cancel_delete_movie")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    
    # Регистрация в базе фильмов
    movies_db.add_or_update_user(user.id, user.username, user.first_name, user.language_code or 'ru')
    
    # Проверяем код игры в параметрах
    if context.args and len(context.args) > 0:
        game_code = context.args[0].upper()
        game = word_game_db.get_game(game_code)
        
        if not game:
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!\n\n"
                f"❌ Игра с кодом `{game_code}` не найдена!\n\n"
                f"🎬 Вы в основном меню бота:",
                reply_markup=create_main_widget(),
                parse_mode='Markdown'
            )
            return
        
        # Проверяем статус игры
        if game['status'] not in ['created', 'collecting']:
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!\n\n"
                f"❌ Игра `{game_code}` уже началась или завершена!\n\n"
                f"🎬 Вы в основном меню бота:",
                reply_markup=create_main_widget(),
                parse_mode='Markdown'
            )
            return
        
        # Проверяем, не в игре ли уже
        existing_player = word_game_db.get_player(game_code, user.id)
        if existing_player:
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!\n\n"
                f"✅ Вы уже в игре `{game_code}`!\n\n"
                f"🎬 Вы в основном меню бота:",
                reply_markup=create_main_widget(),
                parse_mode='Markdown'
            )
            return
        
        # Добавляем игрока
        if word_game_db.add_player(game_code, user.id, user.full_name or user.first_name):
            players = word_game_db.get_players(game_code)
            
            # Уведомляем создателя
            owner_id = game['owner_id']
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"🎉 *{user.first_name} присоединился!*\n\n"
                         f"👥 Игроков: *{len(players)}*\n"
                         f"Код игры: `{game_code}`",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления создателя: {e}")
            
            await update.message.reply_text(
                f"👋 Привет, {user.first_name}!\n\n"
                f"✅ Вы присоединились к игре `{game_code}`!\n"
                f"👥 Игроков: {len(players)}\n\n"
                f"⏳ Ожидайте начала игры...",
                reply_markup=create_main_widget(),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ Ошибка при присоединении к игре!\n\n"
                "🎬 Вы в основном меню бота:",
                reply_markup=create_main_widget()
            )
        return
    
    # Стандартное приветствие
    welcome_text = f"""👋 Привет, {user.first_name}!

🤖 **Универсальный бот**

🎬 **Управление фильмами:**
• Добавление фильмов в список
• Отметка просмотренных с оценкой
• Поиск и сортировка
• Публичные списки
• Удаление фильмов

🎮 **Игра 'Кто я?':**
• Каждый загадывает слово другому
• Угадывайте свое слово по контексту
• Динамическая игра с друзьями

📋 **Основные команды:**
• /menu - вернуться в главное меню
• /game - показать текущие игры
• /help - справка по командам

👇 Используйте кнопки ниже:"""
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=create_main_widget(),
        parse_mode='Markdown'
    )

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /menu - вернуться в главное меню"""
    user = update.effective_user
    
    # Обновляем активность
    movies_db.update_user_activity(user.id)
    
    # Сбрасываем все временные режимы
    if 'delete_movie_mode' in context.user_data:
        del context.user_data['delete_movie_mode']
    if 'delete_movie_list' in context.user_data:
        del context.user_data['delete_movie_list']
    if 'pending_word' in context.user_data:
        del context.user_data['pending_word']
    if 'pending_target_player' in context.user_data:
        del context.user_data['pending_target_player']
    if 'pending_game_code' in context.user_data:
        del context.user_data['pending_game_code']
    
    await update.message.reply_text(
        "🏠 **Главное меню**\n\nВыберите действие:",
        reply_markup=create_main_widget()
    )

async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /game - показать текущие игры"""
    user = update.effective_user
    
    # Обновляем активность
    movies_db.update_user_activity(user.id)
    
    # Ищем активные игры пользователя
    games = word_game_db.get_all_games_for_user(user.id)
    
    if not games:
        await update.message.reply_text(
            "📭 *У вас нет активных игр!*\n\n"
            "🎮 Создайте новую игру или присоединитесь к существующей.",
            reply_markup=create_word_game_main_widget(),
            parse_mode='Markdown'
        )
        return
    
    # Если есть несколько игр, показываем первую
    game = games[0]
    game_code = game['game_id']
    is_owner = game['owner_id'] == user.id
    players = word_game_db.get_players(game_code)
    
    status_texts = {
        'created': '🔄 Ожидание игроков',
        'collecting': '📝 Сбор слов',
        'started': '🎮 Игра идет'
    }
    
    status_text = status_texts.get(game['status'], game['status'])
    
    text = f"🎮 **Текущая игра**\n\n"
    text += f"📝 Код: `{game_code}`\n"
    text += f"📊 Статус: {status_text}\n"
    text += f"👑 Роль: {'Создатель' if is_owner else 'Игрок'}\n\n"
    text += f"👥 *Игроков:* {len(players)}\n"
    
    if game['status'] == 'created':
        reply_markup = create_game_lobby_keyboard(game_code, is_owner)
    elif game['status'] == 'collecting':
        reply_markup = create_waiting_keyboard(game_code)
    else:
        reply_markup = create_game_keyboard(game_code)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь"""
    help_text = """
🤖 **Универсальный бот - помощь**

📋 **Основные команды:**
• /start - начать работу с ботом
• /menu - вернуться в главное меню 🆕
• /game - показать текущие игры 🆕
• /join КОД - присоединиться к игре
• /add - добавить фильм
• /help - показать эту справку

🎬 **Управление фильмами:**
• Напишите название фильма, чтобы добавить его
• Используйте кнопки для управления фильмами
• Формат: "Название, жанр, год"
• Удалять фильмы можно через кнопку "🗑️ Удалить фильм"

🎮 **Игра 'Кто я?':**
• Создатель создает игру и приглашает друзей
• Каждый игрок загадывает слово случайному другому игроку
• Все видят слова, загаданные другим игрокам
• Но не видят слово, загаданное им самим
• Задача - угадать свое слово по контексту!

💡 **Советы:**
• В игре можно загадывать новые слова в любое время
• Чем интереснее слова, тем веселее игра!
• Фильмы и игры работают независимо

🎉 **Удачи в играх и приятного просмотра!**
    """
    
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=create_main_widget()
    )

# ========== ФУНКЦИИ ДЛЯ ФИЛЬМОВ ==========
async def add_movie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add и текстовых сообщений для фильмов"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    # Получение текста
    if context.args:
        text = ' '.join(context.args)
    elif update.message.text and not update.message.text.startswith('/'):
        text = update.message.text
    else:
        await update.message.reply_text(
            "📝 **Добавление фильма**\n\n"
            "Отправьте название фильма.\n\n"
            "Можно указать жанр и год через запятую:\n"
            "• Инцепция\n"
            "• Инцепция, фантастика\n"
            "• Инцепция, фантастика, 2010",
            reply_markup=create_back_widget("main_menu")
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
            reply_markup=create_main_widget()
        )
        return
    
    # Добавление фильма
    movie_id = movies_db.add_movie(user.id, title, genre, year)
    
    if movie_id:
        # Получаем информацию о фильме для подтверждения
        movie_info = movies_db.get_movie_by_id(user.id, movie_id)
        
        response_text = (
            f"✅ **Фильм добавлен!**\n\n"
            f"🎬 **{movie_info['title']}**\n"
        )
        
        if movie_info.get('genre'):
            response_text += f"🏷️ Жанр: {movie_info['genre']}\n"
        
        if movie_info.get('year'):
            response_text += f"📅 Год: {movie_info['year']}\n"
        
        response_text += (
            f"📊 Статус: Хочу посмотреть\n"
            f"👁️ Видимость: {'Публичный' if movie_info['is_public'] else 'Приватный'}\n\n"
            f"Используйте кнопки ниже для управления фильмом."
        )
        
        await update.message.reply_text(
            response_text,
            reply_markup=create_movie_widget(movie_id)
        )
    else:
        await update.message.reply_text(
            "❌ Этот фильм уже есть в вашем списке!",
            reply_markup=create_main_widget()
        )

async def delete_movie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление фильма"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    # Проверяем, не находится ли пользователь уже в режиме удаления фильма
    if 'delete_movie_mode' in context.user_data and context.user_data['delete_movie_mode']:
        # Пользователь в режиме удаления, обрабатываем ввод номера
        await handle_movie_deletion(update, context)
        return
    
    # Если это новый запрос на удаление, показываем список фильмов
    await show_movies_for_deletion(update, context)

async def show_movies_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать фильмы для удаления"""
    user = update.effective_user
    
    # Получаем все фильмы пользователя
    movies = movies_db.get_user_movies(user.id, include_private=True, limit=50)
    
    if not movies:
        await update.message.reply_text(
            "📭 У вас нет фильмов для удаления.",
            reply_markup=create_main_widget()
        )
        return
    
    # Сохраняем список фильмов в контексте
    context.user_data['delete_movie_mode'] = True
    context.user_data['delete_movie_list'] = movies
    
    # Формируем сообщение со списком фильмов
    text = "🗑️ **Удаление фильма**\n\n"
    text += "Выберите номер фильма для удаления:\n\n"
    
    for i, movie in enumerate(movies, 1):
        text += f"{i}. "
        
        # Иконка приватности
        text += "👁️ " if movie.get('is_public', True) else "🔒 "
        
        text += f"{movie['title']}"
        
        if movie.get('genre'):
            text += f" ({movie['genre']})"
        
        if movie.get('year'):
            text += f" [{movie['year']}]"
        
        if movie.get('status') == 'watched':
            text += " ✅"
            
            if movie.get('rating'):
                text += f" ⭐{movie['rating']}/10"
        
        text += "\n"
    
    text += "\n📝 **Введите номер фильма для удаления:**"
    text += "\nИли нажмите кнопку 'Отмена'"
    
    await update.message.reply_text(
        text,
        reply_markup=create_delete_movie_widget()
    )

async def handle_movie_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора фильма для удаления"""
    user = update.effective_user
    text = update.message.text.strip()
    
    # Проверяем, нажал ли пользователь кнопку отмены
    if text.lower() in ['отмена', 'cancel', 'назад', 'back']:
        # Сбрасываем режим удаления
        if 'delete_movie_mode' in context.user_data:
            del context.user_data['delete_movie_mode']
        if 'delete_movie_list' in context.user_data:
            del context.user_data['delete_movie_list']
        
        await update.message.reply_text(
            "❌ Удаление отменено.",
            reply_markup=create_main_widget()
        )
        return
    
    # Проверяем, является ли ввод числом
    if not text.isdigit():
        await update.message.reply_text(
            "❌ Пожалуйста, введите номер фильма (число).",
            reply_markup=create_delete_movie_widget()
        )
        return
    
    movie_index = int(text) - 1
    
    # Проверяем, существует ли фильм с таким номером
    if ('delete_movie_list' not in context.user_data or 
        movie_index < 0 or 
        movie_index >= len(context.user_data['delete_movie_list'])):
        
        await update.message.reply_text(
            "❌ Неверный номер фильма. Пожалуйста, введите номер из списка.",
            reply_markup=create_delete_movie_widget()
        )
        return
    
    # Получаем информацию о фильме
    movie = context.user_data['delete_movie_list'][movie_index]
    movie_id = movie['id']
    movie_title = movie['title']
    
    # Удаляем фильм
    success = movies_db.delete_movie(user.id, movie_id)
    
    # Сбрасываем режим удаления
    if 'delete_movie_mode' in context.user_data:
        del context.user_data['delete_movie_mode']
    if 'delete_movie_list' in context.user_data:
        del context.user_data['delete_movie_list']
    
    if success:
        await update.message.reply_text(
            f"✅ Фильм \"{movie_title}\" успешно удален!",
            reply_markup=create_main_widget()
        )
    else:
        await update.message.reply_text(
            "❌ Не удалось удалить фильм. Пожалуйста, попробуйте снова.",
            reply_markup=create_main_widget()
        )

async def show_my_movies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все фильмы пользователя"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
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
    
    want_movies = movies_db.get_user_movies(user.id, status='want_to_watch', genre=genre_filter, year=year_filter, limit=15)
    watched_movies = movies_db.get_user_movies(user.id, status='watched', genre=genre_filter, year=year_filter, limit=10)
    
    # Получаем статистику
    stats = movies_db.get_user_stats(user.id)
    
    # Формируем ответ
    text = f"🎬 **Ваши фильмы**\n\n"
    
    if genre_filter:
        text += f"🏷️ Фильтр: {genre_filter}\n"
    if year_filter:
        text += f"📅 Фильтр: {year_filter} год\n"
    
    text += f"📝 **Хочу посмотреть ({len(want_movies)}):**\n"
    text += format_movie_list(want_movies, show_status=False, show_privacy=True)
    
    text += f"\n✅ **Просмотрено ({len(watched_movies)}):**\n"
    text += format_movie_list(watched_movies, show_status=True, show_privacy=True)
    
    if stats['want_count'] > 15 or stats['watched_count'] > 10:
        text += f"\n📄 Для просмотра всех фильмов используйте поиск."
    
    text += f"\n📊 **Статистика:**\n"
    text += f"• Всего: {stats['want_count'] + stats['watched_count']}\n"
    text += f"• Хочу посмотреть: {stats['want_count']}\n"
    text += f"• Просмотрено: {stats['watched_count']}\n"
    
    if stats['rated_count'] > 0:
        text += f"• Средняя оценка: {stats['avg_rating']}/10"
    
    await update.message.reply_text(text, reply_markup=create_movies_widget())

async def show_watched_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать просмотренные фильмы"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    watched_movies = movies_db.get_user_movies(user.id, status='watched', limit=20)
    stats = movies_db.get_user_stats(user.id)
    
    text = f"✅ **Просмотренные фильмы ({stats['watched_count']})**\n\n"
    
    if watched_movies:
        # Сортируем по рейтингу или дате просмотра
        watched_movies_sorted = sorted(
            watched_movies, 
            key=lambda x: (x.get('rating') or 0, x.get('added_date') or ''), 
            reverse=True
        )
        
        text += format_movie_list(watched_movies_sorted, show_status=False, show_privacy=True)
        
        if stats['rated_count'] > 0:
            text += f"\n⭐ **Средняя ваша оценка:** {stats['avg_rating']}/10"
    else:
        text += "У вас еще нет просмотренных фильмов.\nДобавьте фильмы и отметьте их как просмотренные!"
    
    keyboard = [
        [
            InlineKeyboardButton("🏆 Топ по оценкам", callback_data="top_rated"),
            InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies")
        ],
        [
            InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie"),
            InlineKeyboardButton("🗑️ Удалить фильм", callback_data="delete_movie")
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_public_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать публичный список фильмов"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    # Фильтрация по аргументам
    genre_filter = None
    year_filter = None
    
    if context.args:
        for arg in context.args:
            if arg.isdigit() and len(arg) == 4:
                year_filter = int(arg)
            else:
                genre_filter = arg
    
    public_movies = movies_db.get_public_movies(limit=20, genre=genre_filter, year=year_filter)
    global_stats = movies_db.get_global_stats()
    top_genres = movies_db.get_top_genres(limit=5)
    
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
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск фильмов"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    if not context.args:
        await update.message.reply_text(
            "🔍 **Поиск фильмов**\n\n"
            "Использование:\n"
            "/search <запрос> - поиск в вашем списке\n"
            "/search_public <запрос> - поиск в публичном списке\n\n"
            "Примеры:\n"
            "/search матрица\n"
            "/search_public криминальное чтиво",
            reply_markup=create_main_widget()
        )
        return
    
    query = ' '.join(context.args)
    movies = movies_db.search_movies(user.id, query, search_in_public=False)
    
    text = f"🔍 **Результаты поиска: \"{query}\"**\n\n"
    
    if movies:
        text += format_movie_list(movies, show_status=True, show_privacy=True)
    else:
        text += "Ничего не найдено.\nПопробуйте другой запрос."
    
    keyboard = [
        [
            InlineKeyboardButton("🔍 Поиск в публичном", callback_data="search_public_menu"),
            InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies")
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def search_public_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск в публичном списке"""
    user = update.effective_user
    movies_db.update_user_activity(user.id)
    
    if not context.args:
        await update.message.reply_text(
            "🔍 **Поиск в публичном списке**\n\n"
            "Напишите запрос для поиска фильмов среди публичных списков всех пользователей.",
            reply_markup=create_back_widget("public_list")
        )
        return
    
    query = ' '.join(context.args)
    movies = movies_db.search_movies(user.id, query, search_in_public=True)
    
    text = f"🔍 **Результаты поиска: \"{query}\"**\n\n"
    
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
    movies_db.update_user_activity(user.id)
    
    movie = movies_db.get_random_movie(user.id, 'want_to_watch')
    
    if movie:
        text = f"🎲 **Случайный фильм для просмотра:**\n\n"
        text += f"🎬 **{movie['title']}**\n"
        
        if movie.get('genre'):
            text += f"🏷️ Жанр: {movie['genre']}\n"
        
        text += "\nХотите посмотреть этот фильм?"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, смотрю!", callback_data=f"watch_{movie['id']}"),
                InlineKeyboardButton("🎲 Другой фильм", callback_data="random_movie")
            ],
            [
                InlineKeyboardButton("📋 К списку", callback_data="my_movies"),
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
            ]
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
    movies_db.update_user_activity(user.id)
    
    # Получаем статистику
    user_stats = movies_db.get_user_stats(user.id)
    global_stats = movies_db.get_global_stats()
    user_genres = movies_db.get_user_genres(user.id)
    top_genres = movies_db.get_top_genres(limit=5)
    
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
            InlineKeyboardButton("🎲 Случайный фильм", callback_data="random_movie")
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ========== ФУНКЦИИ ДЛЯ ИГРЫ "КТО Я?" ==========
async def new_word_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание новой игры"""
    user = update.effective_user
    
    # Генерируем код игры
    game_code = generate_game_code()
    
    # Создаем игру
    if word_game_db.create_game(game_code, user.id):
        word_game_db.add_player(game_code, user.id, user.first_name)
        
        await update.message.reply_text(
            f"🎮 *Игра 'Кто я?' создана!*\n\n"
            f"📝 *Код игры:* `{game_code}`\n\n"
            f"👤 *Игроки:*\n"
            f"1. {user.first_name} 👑\n\n"
            f"📋 *Для приглашения:*\n"
            f"• Отправьте друзьям команду:\n"
            f"`/join {game_code}`\n"
            f"• Или ссылку:\n"
            f"`t.me/{(await context.bot.get_me()).username}?start={game_code}`\n\n"
            f"⏳ Ждем игроков...",
            reply_markup=create_game_lobby_keyboard(game_code, is_owner=True),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Ошибка при создании игры!")

async def join_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Присоединение к игре"""
    user = update.effective_user
    
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите код игры!\nПример: `/join ABCD`",
            parse_mode='Markdown'
        )
        return
    
    game_code = context.args[0].upper()
    game = word_game_db.get_game(game_code)
    
    if not game:
        await update.message.reply_text("❌ Игра не найдена!")
        return
    
    if game['status'] not in ['created', 'collecting']:
        await update.message.reply_text("❌ Игра уже началась!")
        return
    
    # Проверяем, не присоединился ли уже
    if word_game_db.get_player(game_code, user.id):
        await update.message.reply_text("✅ Вы уже в игре!")
        return
    
    # Добавляем игрока
    if word_game_db.add_player(game_code, user.id, user.full_name or user.first_name):
        players = word_game_db.get_players(game_code)
        
        # Уведомляем создателя
        owner_id = game['owner_id']
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=f"🎉 *{user.first_name} присоединился!*\n\n"
                     f"👥 Игроков: *{len(players)}*\n"
                     f"Код игры: `{game_code}`",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления создателя: {e}")
        
        await update.message.reply_text(
            f"✅ Вы присоединились к игре `{game_code}`!\n"
            f"👥 Игроков: {len(players)}\n"
            f"⏳ Ожидайте начала игры...",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Ошибка при присоединении к игре!")

async def start_game_collecting(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Начать сбор слов"""
    query = update.callback_query
    if query:
        await query.answer()
    
    game = word_game_db.get_game(game_code)
    if not game:
        if query:
            await query.edit_message_text("❌ Игра не найдена!")
        return
    
    players = word_game_db.get_players(game_code)
    
    if len(players) < 2:
        if query:
            await query.edit_message_text("❌ Нужно минимум 2 игрока!")
        else:
            await context.bot.send_message(
                chat_id=game['owner_id'],
                text="❌ Нужно минимум 2 игрока!"
            )
        return
    
    # Очищаем старые пары
    word_game_db.clear_player_pairs(game_code)
    
    # Меняем статус
    word_game_db.update_game_status(game_code, 'collecting')
    
    # Создаем случайные пары игроков
    player_ids = [p['user_id'] for p in players]
    player_names = {p['user_id']: p['user_name'] for p in players}
    
    # Если всего 2 игрока, они загадывают друг другу
    if len(player_ids) == 2:
        pairs = [(player_ids[0], player_ids[1]), (player_ids[1], player_ids[0])]
    else:
        # Для 3+ игроков: каждый загадывает другому игроку
        pairs = []
        shuffled_players = player_ids.copy()
        random.shuffle(shuffled_players)
        
        for i in range(len(player_ids)):
            from_player = player_ids[i]
            to_player = shuffled_players[i]
            
            # Если игрок загадывает себе, находим другого
            if from_player == to_player:
                possible_targets = [p for p in player_ids if p != from_player]
                if possible_targets:
                    to_player = random.choice(possible_targets)
            
            pairs.append((from_player, to_player))
    
    # Сохраняем пары в базе данных
    for from_user_id, to_user_id in pairs:
        word_game_db.add_player_pair(game_code, from_user_id, to_user_id)
    
    # Отправляем запрос слов всем игрокам
    for player in players:
        try:
            # Находим, кому загадывает этот игрок
            pair = word_game_db.get_player_pair(game_code, player['user_id'])
            
            if pair:
                to_user_id = pair['to_user_id']
                to_player_name = player_names.get(to_user_id, "Неизвестный игрок")
                await context.bot.send_message(
                    chat_id=player['user_id'],
                    text=f"🎮 *Игра начинается!*\n\n"
                         f"Вы загадываете слово игроку:\n"
                         f"👤 *{to_player_name}*\n\n"
                         f"📝 *Напишите слово или фразу:*\n"
                         f"(одним сообщением)",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Ошибка отправки игроку {player['user_id']}: {e}")
    
    # Уведомляем создателя
    if query:
        await query.edit_message_text(
            f"✅ *Сбор слов начат!*\n\n"
            f"📝 Все игроки получили запрос на ввод слова.\n"
            f"👥 Игроков: {len(players)}\n\n"
            f"⏳ Ожидаем, пока все введут слова...",
            reply_markup=create_waiting_keyboard(game_code),
            parse_mode='Markdown'
        )
    else:
        await context.bot.send_message(
            chat_id=game['owner_id'],
            text=f"✅ *Сбор слов начат!*\n\n"
                 f"📝 Все игроки получили запрос на ввод слова.\n"
                 f"👥 Игроков: {len(players)}\n\n"
                 f"⏳ Ожидаем, пока все введут слова...",
            reply_markup=create_waiting_keyboard(game_code),
            parse_mode='Markdown'
        )

async def handle_word_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка отправки слова"""
    user_id = update.effective_user.id
    word = update.message.text.strip()
    
    if not word or len(word) < 2:
        await update.message.reply_text("❌ Слово слишком короткое!")
        return
    
    # Проверяем, находится ли пользователь в состоянии ожидания выбора игрока для загадывания
    if 'pending_word' in context.user_data and 'pending_target_player' in context.user_data:
        # Это ответ на запрос загадать слово конкретному игроку
        target_user_id = context.user_data['pending_target_player']
        game_code = context.user_data['pending_game_code']
        
        # Сохраняем слово
        word_game_db.add_player_word(game_code, user_id, target_user_id, word)
        
        # Получаем имена для уведомлений
        target_player = word_game_db.get_player(game_code, target_user_id)
        target_name = target_player['user_name'] if target_player else "Неизвестный игрок"
        
        # Уведомляем текущий игрок
        await update.message.reply_text(
            f"✅ *Слово загадано!*\n\n"
            f"Вы загадали игроку *{target_name}* слово: *{word}*",
            parse_mode='Markdown'
        )
        
        # Уведомляем целевого игрока
        try:
            from_player = word_game_db.get_player(game_code, user_id)
            from_name = from_player['user_name'] if from_player else "Неизвестный игрок"
            
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"🎁 *Новое слово!*\n\n"
                     f"Игрок *{from_name}* загадал вам новое слово!\n\n"
                     f"🎯 Попробуйте его угадать!",
                reply_markup=create_game_keyboard(game_code),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления игрока {target_user_id}: {e}")
        
        # Очищаем временные данные
        del context.user_data['pending_word']
        del context.user_data['pending_target_player']
        del context.user_data['pending_game_code']
        
        return
    
    # Ищем игру, в которой пользователь участвует и собирает слова
    games = word_game_db.get_all_games_for_user(user_id)
    active_game = None
    
    for game in games:
        if game['status'] == 'collecting':
            active_game = game
            break
    
    if not active_game:
        # Проверяем, не пытается ли игрок угадать слово
        await process_guess_attempt(update, context, word)
        return
    
    game_code = active_game['game_id']
    
    # Получаем пару игрока
    pair = word_game_db.get_player_pair(game_code, user_id)
    
    if not pair:
        await update.message.reply_text("❌ Ошибка: не найдена пара игроков!")
        return
    
    to_user_id = pair['to_user_id']
    
    # Сохраняем слово
    word_game_db.add_player_word(game_code, user_id, to_user_id, word)
    
    # Сохраняем слово, которое этот игрок загадал для других
    word_game_db.set_player_word_for_others(game_code, user_id, word)
    
    # Проверяем, все ли слова собраны
    all_words = word_game_db.get_all_player_words(game_code)
    pairs_count = word_game_db.get_pairs_count(game_code)
    
    if len(all_words) == pairs_count:
        # Все слова собраны, начинаем игру
        word_game_db.update_game_status(game_code, 'started')
        
        # Отправляем результаты всем игрокам
        await show_game_results(game_code, context)
    else:
        # Ждем остальные слова
        await update.message.reply_text(
            f"✅ *Слово принято!*\n\n"
            f"Вы загадали: *{word}*\n\n"
            f"⏳ Ожидаем остальных игроков...\n"
            f"📊 Собрано слов: {len(all_words)}/{pairs_count}",
            parse_mode='Markdown'
        )

async def show_game_results(game_code: str, context: ContextTypes.DEFAULT_TYPE):
    """Показать результаты игры всем игрокам"""
    game = word_game_db.get_game(game_code)
    if not game:
        return
    
    players = word_game_db.get_players(game_code)
    
    # Для каждого игрока создаем индивидуальное сообщение
    for player in players:
        try:
            # Получаем слова, видимые этому игроку
            visible_words = word_game_db.get_visible_words_for_player(game_code, player['user_id'])
            
            # Формируем список видимых слов с никнеймами
            words_text = ""
            if visible_words:
                words_text = "🔍 *Слова других игроков:*\n"
                for word_data in visible_words:
                    from_name = word_data.get('from_name', 'Неизвестный')
                    to_name = word_data.get('to_name', 'Неизвестный')
                    word_text = word_data['word']
                    words_text += f"👤 {from_name} → {to_name}: *{word_text}*\n"
            
            # Получаем количество слов для угадывания
            words_to_guess = word_game_db.get_player_words_count(game_code, player['user_id'])
            
            # Отправляем игроку
            await context.bot.send_message(
                chat_id=player['user_id'],
                text=f"🎮 *Игра началась!*\n\n"
                     f"📊 *Ваша задача:*\n"
                     f"Угадать, какое слово вам загадали!\n\n"
                     f"{words_text}\n"
                     f"❓ *Вам загадали слов:* {words_to_guess}\n\n"
                     f"💡 Вы можете:\n"
                     f"• Угадывать свои слова\n"
                     f"• Загадывать слова другим игрокам\n"
                     f"• Смотреть слова других игрокам",
                reply_markup=create_game_keyboard(game_code),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки игроку {player['user_id']}: {e}")
    
    # Уведомление создателя
    await context.bot.send_message(
        chat_id=game['owner_id'],
        text=f"✅ *Все слова собраны!*\n\n"
             f"🎮 Игра началась!\n"
             f"👥 Игроков: {len(players)}\n"
             f"📊 Слов загадано: {len(word_game_db.get_all_player_words(game_code))}",
        reply_markup=create_game_keyboard(game_code),
        parse_mode='Markdown'
    )

async def show_all_words(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Показать все слова (кроме загаданных текущему игроку)"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    game = word_game_db.get_game(game_code)
    
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    # Получаем слова, видимые этому игроку
    visible_words = word_game_db.get_visible_words_for_player(game_code, user_id)
    
    # Получаем количество слов для угадывания
    words_to_guess = word_game_db.get_player_words_count(game_code, user_id)
    
    # Формируем сообщение
    words_text = "🔍 *Слова в игре:*\n\n"
    
    if visible_words:
        for word_data in visible_words:
            from_name = word_data.get('from_name', 'Неизвестный')
            to_name = word_data.get('to_name', 'Неизвестный')
            word = word_data['word']
            words_text += f"👤 {from_name} → {to_name}: *{word}*\n"
    else:
        words_text += "📭 Пока нет слов для отображения\n"
    
    words_text += f"\n❓ *Вам загадали слов:* {words_to_guess}\n\n"
    words_text += "💡 Вы можете угадывать свои слова или загадывать слова другим игрокам!"
    
    await query.edit_message_text(
        text=words_text,
        reply_markup=create_game_keyboard(game_code),
        parse_mode='Markdown'
    )

async def process_guess_attempt(update: Update, context: ContextTypes.DEFAULT_TYPE, guess_word: str):
    """Обработка попытки угадать слово"""
    user_id = update.effective_user.id
    
    # Ищем активную игру пользователя
    games = word_game_db.get_all_games_for_user(user_id)
    active_game = None
    
    for game in games:
        if game['status'] == 'started':
            active_game = game
            break
    
    if not active_game:
        # Если нет активной игры, возможно это попытка угадать фильм
        # или обычное сообщение
        return False
    
    game_code = active_game['game_id']
    
    # Получаем текущее слово для угадывания
    word_data = word_game_db.get_word_for_player(game_code, user_id)
    
    if not word_data:
        await update.message.reply_text(
            "🎉 *Вы угадали все слова!*\n\n"
            "✨ Теперь вы можете загадывать слова другим игрокам!\n"
            "📝 Используйте кнопку 'Загадать слово'",
            parse_mode='Markdown'
        )
        return True
    
    word_id, correct_word, from_user_id, is_guessed = word_data
    
    # Проверяем угадано ли слово (регистр не важен)
    if guess_word.lower().strip() == correct_word.lower().strip():
        # Помечаем слово как угаданное
        if word_game_db.mark_word_as_guessed(word_id, game_code, user_id):
            # Получаем информацию об авторе слова
            players = word_game_db.get_players(game_code)
            from_player_name = next((p['user_name'] for p in players if p['user_id'] == from_user_id), "Неизвестный игрок")
            
            await update.message.reply_text(
                f"🎉 *Правильно!*\n\n"
                f"✅ Вы угадали слово: *{correct_word}*\n"
                f"👤 Слово загадал: *{from_player_name}*",
                parse_mode='Markdown'
            )
            
            # Проверяем, остались ли еще слова для угадывания
            remaining_words = word_game_db.get_player_words_count(game_code, user_id)
            if remaining_words > 0:
                await update.message.reply_text(
                    f"🎯 *Осталось слов для угадывания:* {remaining_words}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    "🎉 *Вы угадали все слова!*\n\n"
                    "✨ Теперь вы можете загадывать слова другим игрокам!\n"
                    "📝 Используйте кнопку 'Загадать слово'",
                    parse_mode='Markdown'
                )
            return True
        else:
            await update.message.reply_text("❌ Ошибка при обработке угаданного слова!")
            return False
    else:
        await update.message.reply_text(
            f"❌ *Неправильно!*\n\n"
            f"Попробуйте еще раз.\n"
            f"Ваша попытка: *{guess_word}*",
            parse_mode='Markdown'
        )
        return False

async def show_player_words(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Показать игроку его текущие слова для угадывания"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    game = word_game_db.get_game(game_code)
    
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    # Получаем активные слова для игрока
    active_words = word_game_db.get_all_unguessed_words_for_player(game_code, user_id)
    
    if not active_words:
        await query.edit_message_text(
            "🎉 *Вы угадали все слова!*\n\n"
            "✨ Теперь вы можете загадывать слова другим игрокам!\n"
            "📝 Используйте кнопку 'Загадать слово'",
            reply_markup=create_guess_keyboard(game_code),
            parse_mode='Markdown'
        )
        return
    
    # Получаем слова, видимые этому игроку
    visible_words = word_game_db.get_visible_words_for_player(game_code, user_id)
    
    # Формируем сообщение
    words_text = "🔍 *Слова в игре:*\n\n"
    
    if visible_words:
        for word_data in visible_words:
            from_name = word_data.get('from_name', 'Неизвестный')
            to_name = word_data.get('to_name', 'Неизвестный')
            word = word_data['word']
            words_text += f"👤 {from_name} → {to_name}: *{word}*\n"
    
    words_text += f"\n❓ *Вам загадали слов:* {len(active_words)}\n\n"
    words_text += "🎯 Попробуйте угадать свое слово!"
    
    await query.edit_message_text(
        text=words_text,
        reply_markup=create_guess_keyboard(game_code),
        parse_mode='Markdown'
    )

async def give_word_to_player(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Выбор игрока для загадывания слова"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    game = word_game_db.get_game(game_code)
    
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    # Получаем список игроков, которым можно загадать слово
    available_players = word_game_db.get_players_to_guess_for(game_code, user_id)
    
    if not available_players:
        await query.edit_message_text(
            "🎉 *Вы уже загадали слова всем игрокам!*\n\n"
            "⏳ Подождите, пока кто-то угадает ваши слова и попросит новое.",
            reply_markup=create_game_keyboard(game_code),
            parse_mode='Markdown'
        )
        return
    
    await query.edit_message_text(
        "👥 *Выберите игрока для загадывания слова:*",
        reply_markup=create_player_selection_keyboard(game_code, available_players),
        parse_mode='Markdown'
    )

async def select_player_for_word(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str, target_user_id: int):
    """Выбор конкретного игрока для загадывания слова"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    game = word_game_db.get_game(game_code)
    
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    # Проверяем, можно ли загадать слово этому игроку
    available_players = word_game_db.get_players_to_guess_for(game_code, user_id)
    target_is_available = any(pid == target_user_id for pid, _ in available_players)
    
    if not target_is_available:
        await query.edit_message_text(
            "❌ *Нельзя загадать слово этому игроку!*\n\n"
            "Возможно, вы уже загадали ему слово, которое еще не угадано.",
            reply_markup=create_game_keyboard(game_code),
            parse_mode='Markdown'
        )
        return
    
    # Получаем имя игрока
    target_player = word_game_db.get_player(game_code, target_user_id)
    if not target_player:
        await query.edit_message_text("❌ Игрок не найден!")
        return
    
    # Сохраняем информацию в контекст
    context.user_data['pending_word'] = True
    context.user_data['pending_target_player'] = target_user_id
    context.user_data['pending_game_code'] = game_code
    
    await query.edit_message_text(
        f"📝 *Загадываем слово игроку:*\n"
        f"👤 *{target_player['user_name']}*\n\n"
        f"✍️ *Напишите слово или фразу:*\n"
        f"(одним сообщением)",
        parse_mode='Markdown'
    )

async def show_players(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Показать игроков"""
    query = update.callback_query
    await query.answer()
    
    game = word_game_db.get_game(game_code)
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    players = word_game_db.get_players(game_code)
    game_status = game['status']
    
    status_texts = {
        'created': '🔄 Ожидание игроков',
        'collecting': '📝 Сбор слов',
        'started': '🎮 Игра идет'
    }
    
    status_text = status_texts.get(game_status, game_status)
    
    text = f"👥 *Игроки ({len(players)})*\n\n"
    for idx, player in enumerate(players, 1):
        role = "👑" if player['user_id'] == game['owner_id'] else "👤"
        words_guessed = player['words_received']
        text += f"{idx}. {player['user_name']} {role} - Угадано слов: {words_guessed}\n"
    
    text += f"\n📊 *Статус:* {status_text}\n"
    text += f"📝 *Игра продолжается до завершения создателем*\n"
    
    if game_status == 'started':
        await query.edit_message_text(
            text=text,
            reply_markup=create_game_keyboard(game_code),
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            text=text,
            parse_mode='Markdown'
        )

async def end_game(update: Update, context: ContextTypes.DEFAULT_TYPE, game_code: str):
    """Завершить игру и показать все слова"""
    query = update.callback_query
    await query.answer()
    
    game = word_game_db.get_game(game_code)
    if not game:
        await query.edit_message_text("❌ Игра не найдена!")
        return
    
    players = word_game_db.get_players(game_code)
    all_words = word_game_db.get_all_player_words(game_code)
    
    # Отправляем финальные результаты всем игроков
    for player in players:
        try:
            # Получаем слова, которые загадали этому игроку
            player_words = []
            for word_data in all_words:
                if word_data['to_user_id'] == player['user_id']:
                    from_name = word_data.get('from_name', 'Неизвестный')
                    word = word_data['word']
                    is_guessed = word_data['is_guessed']
                    status = "✅ Угадано" if is_guessed else "❌ Не угадано"
                    player_words.append(f"👤 {from_name}: *{word}* ({status})")
            
            # Формируем полный список слов
            final_text = f"🏁 *Игра завершена!*\n\n"
            
            if player_words:
                final_text += f"🎯 *Вам загадывали:*\n" + "\n".join(player_words) + "\n\n"
            
            final_text += f"📊 *Всего слов угадано:* {player['words_received']}\n\n"
            final_text += "🎮 Спасибо за игру! 🎉"
            
            await context.bot.send_message(
                chat_id=player['user_id'],
                text=final_text,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки игроку {player['user_id']}: {e}")
    
    # Удаляем игру
    word_game_db.delete_game(game_code)
    
    await query.edit_message_text(
        "✅ *Игра завершена!*\n\nВсе игроки получили результаты.\n\nВозвращаемся в главное меню...",
        reply_markup=create_main_widget(),
        parse_mode='Markdown'
    )

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    # Обновляем активность в базе фильмов
    movies_db.update_user_activity(user.id)
    
    try:
        # Основное меню
        if data == "main_menu":
            # Очищаем временные данные
            if 'delete_movie_mode' in context.user_data:
                del context.user_data['delete_movie_mode']
            if 'delete_movie_list' in context.user_data:
                del context.user_data['delete_movie_list']
            if 'pending_word' in context.user_data:
                del context.user_data['pending_word']
            if 'pending_target_player' in context.user_data:
                del context.user_data['pending_target_player']
            if 'pending_game_code' in context.user_data:
                del context.user_data['pending_game_code']
            
            await query.edit_message_text(
                "🏠 **Главное меню**\n\nВыберите действие:",
                reply_markup=create_main_widget()
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
                reply_markup=create_back_widget("my_movies")
            )
        
        elif data == "search_public_menu":
            await query.edit_message_text(
                "🔍 **Поиск в публичном списке**\n\n"
                "Отправьте сообщение с названием фильма для поиска среди публичных фильмов всех пользователей.",
                reply_markup=create_back_widget("public_list")
            )
        
        elif data == "search_public":
            await query.edit_message_text(
                "🔍 **Поиск в публичном списке**\n\n"
                "Отправьте сообщение с названием фильма для поиска.",
                reply_markup=create_back_widget("public_list")
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
                reply_markup=create_back_widget("main_menu")
            )
        
        # Удалить фильм
        elif data == "delete_movie":
            # Сбрасываем режим удаления если он был
            if 'delete_movie_mode' in context.user_data:
                del context.user_data['delete_movie_mode']
            if 'delete_movie_list' in context.user_data:
                del context.user_data['delete_movie_list']
            
            # Показываем список фильмов для удаления
            await show_movies_for_deletion_callback(query, context)
        
        elif data == "cancel_delete_movie":
            # Сбрасываем режим удаления
            if 'delete_movie_mode' in context.user_data:
                del context.user_data['delete_movie_mode']
            if 'delete_movie_list' in context.user_data:
                del context.user_data['delete_movie_list']
            
            await query.edit_message_text(
                "❌ Удаление отменено.",
                reply_markup=create_main_widget()
            )
        
        # Игра "Кто я?"
        elif data == "word_game":
            await query.edit_message_text(
                "🎮 **Игра 'Кто я?'**\n\n"
                "Выберите действие:",
                reply_markup=create_word_game_main_widget()
            )
        
        elif data == "current_games":
            await handle_current_games(query, user.id, context)
        
        elif data == "new_word_game":
            # Создаем новую игру
            game_code = generate_game_code()
            
            if word_game_db.create_game(game_code, user.id):
                word_game_db.add_player(game_code, user.id, user.first_name)
                
                await query.edit_message_text(
                    f"🎮 *Игра 'Кто я?' создана!*\n\n"
                    f"📝 *Код игры:* `{game_code}`\n\n"
                    f"👤 *Игроки:*\n"
                    f"1. {user.first_name} 👑\n\n"
                    f"📋 *Для приглашения:*\n"
                    f"• Отправьте друзьям команду:\n"
                    f"`/join {game_code}`\n"
                    f"• Или ссылку:\n"
                    f"`t.me/{(await context.bot.get_me()).username}?start={game_code}`\n\n"
                    f"⏳ Ждем игроков...",
                    reply_markup=create_game_lobby_keyboard(game_code, is_owner=True),
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text("❌ Ошибка при создании игры!")
        
        elif data == "my_word_games":
            # Ищем активные игры
            games = word_game_db.get_all_games_for_user(user.id)
            
            if not games:
                await query.edit_message_text(
                    "📭 *Нет активных игр!*\n"
                    "Создайте новую игру или присоединитесь к существующей.",
                    reply_markup=create_word_game_main_widget(),
                    parse_mode='Markdown'
                )
                return
            
            # Показываем первую активную игру
            game = games[0]
            game_code = game['game_id']
            is_owner = game['owner_id'] == user.id
            
            players = word_game_db.get_players(game_code)
            
            status_texts = {
                'created': '🔄 Ожидание игроков',
                'collecting': '📝 Сбор слов',
                'started': '🎮 Игра идет'
            }
            
            status_text = status_texts.get(game['status'], game['status'])
            
            text = f"🎮 *Активная игра*\n\n"
            text += f"📝 Код: `{game_code}`\n"
            text += f"📊 Статус: {status_text}\n"
            text += f"👑 Роль: {'Создатель' if is_owner else 'Игрок'}\n\n"
            text += f"👥 *Игроков:* {len(players)}\n"
            
            if game['status'] == 'created':
                await query.edit_message_text(
                    text,
                    reply_markup=create_game_lobby_keyboard(game_code, is_owner),
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    text,
                    reply_markup=create_game_keyboard(game_code),
                    parse_mode='Markdown'
                )
        
        elif data == "join_word_game":
            await query.edit_message_text(
                "🔗 **Присоединение к игре**\n\n"
                "Для присоединения к игре отправьте команду:\n"
                "`/join КОД_ИГРЫ`\n\n"
                "Или попросите у друга ссылку на игру.",
                reply_markup=create_back_widget("word_game"),
                parse_mode='Markdown'
            )
        
        elif data == "word_game_rules":
            await query.edit_message_text(
                "📖 **Правила игры 'Кто я?'**\n\n"
                "1. Создатель создает игру и приглашает друзей\n"
                "2. Каждый игрок загадывает слово случайному другому игроку\n"
                "3. Все видят слова, загаданные другим игрокам с никнеймами\n"
                "4. Но не видят слово, загаданное им самим\n"
                "5. Задача - угадать свое слово по контексту!\n"
                "6. Можно загадывать новые слова другим игрокам в любое время!\n"
                "7. Игра продолжается до ручного завершения создателем\n\n"
                "💡 **Советы:**\n"
                "• Слова могут быть любыми: предметы, имена, понятия\n"
                "• Чем интереснее слова, тем веселее игра!\n"
                "• Попробуйте угадать свое слово по контексту других слов\n"
                "• Если угадали все слова - загадывайте новые другим игрокам!",
                reply_markup=create_back_widget("word_game"),
                parse_mode='Markdown'
            )
        
        # Помощь
        elif data == "help":
            await handle_help(query)
        
        # Жанры
        elif data == "show_genres":
            top_genres = movies_db.get_top_genres(limit=10)
            
            text = "🏷️ **Популярные жанры:**\n\n"
            for genre, count in top_genres:
                text += f"• {genre}: {count} фильмов\n"
            
            keyboard = [
                [InlineKeyboardButton("👁️ Публичный список", callback_data="public_list")],
                [InlineKeyboardButton("🔙 Назад", callback_data="public_list")]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif data == "my_genres":
            user_genres = movies_db.get_user_genres(user.id)
            
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
            want_movies = movies_db.get_user_movies(user.id, status='want_to_watch', genre=genre, limit=10)
            watched_movies = movies_db.get_user_movies(user.id, status='watched', genre=genre, limit=5)
            
            text = f"🏷️ **Фильмы в жанре: {genre}**\n\n"
            text += f"📝 **Хочу посмотреть ({len(want_movies)}):**\n"
            text += format_movie_list(want_movies, show_status=False, show_privacy=True)
            
            text += f"\n✅ **Просмотрено ({len(watched_movies)}):**\n"
            text += format_movie_list(watched_movies, show_status=True)
            
            keyboard = [
                [InlineKeyboardButton("🔙 К жанрам", callback_data="my_genres")],
                [InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies")],
                [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        # Топ по оценкам
        elif data == "top_rated":
            watched_movies = movies_db.get_user_movies(user.id, status='watched')
            
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
                [
                    InlineKeyboardButton("✅ Просмотренные", callback_data="watched"),
                    InlineKeyboardButton("📋 Все фильмы", callback_data="my_movies")
                ],
                [
                    InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
                ]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        # Управление фильмом
        elif data.startswith("watch_"):
            await handle_watch_button(query, user.id, data)
        
        elif data.startswith("private_"):
            await handle_private_button(query, user.id, data)
        
        elif data.startswith("delete_"):
            await handle_delete_button(query, user.id, data)
        
        elif data.startswith("rate_"):
            parts = data.split("_")
            movie_id = int(parts[1])
            rating = int(parts[2])
            
            if rating > 0:
                success = movies_db.mark_as_watched(user.id, movie_id, rating=rating)
                if success:
                    movie = movies_db.get_movie_by_id(user.id, movie_id)
                    text = f"✅ Фильм \"{movie['title']}\" отмечен как просмотренный с оценкой ⭐{rating}/10!\n\n"
                    text += "Спасибо за оценку!"
                else:
                    text = "❌ Не удалось поставить оценку."
            else:
                success = movies_db.mark_as_watched(user.id, movie_id)
                if success:
                    movie = movies_db.get_movie_by_id(user.id, movie_id)
                    text = f"✅ Фильм \"{movie['title']}\" отмечен как просмотренный без оценки.\n\n"
                else:
                    text = "❌ Не удалось отметить фильм как просмотренный."
            
            keyboard = [
                [
                    InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
                    InlineKeyboardButton("✅ Просмотренные", callback_data="watched")
                ],
                [
                    InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
                ]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        elif data.startswith("movie_back_"):
            movie_id = int(data.split("_")[2])
            movie = movies_db.get_movie_by_id(user.id, movie_id)
            
            if movie:
                text = f"🎬 **{movie['title']}**\n\n"
                
                if movie.get('genre'):
                    text += f"🏷️ Жанр: {movie['genre']}\n"
                
                if movie.get('year'):
                    text += f"📅 Год: {movie['year']}\n"
                
                if movie.get('notes'):
                    text += f"📝 Заметки: {movie['notes']}\n"
                
                text += f"📊 Статус: {'Просмотрен ✅' if movie['status'] == 'watched' else 'Хочу посмотреть'}\n"
                text += f"👁️ Видимость: {'Публичный' if movie['is_public'] else 'Приватный'}\n\n"
                text += "Используйте кнопки для управления:"
                
                await query.edit_message_text(
                    text,
                    reply_markup=create_movie_widget(movie_id)
                )
        
        # Обработка кнопок игры "Кто я?"
        elif data.startswith('start_'):
            game_code = data.split('_')[1]
            game = word_game_db.get_game(game_code)
            if game and query.from_user.id == game['owner_id']:
                await start_game_collecting(update, context, game_code)
            else:
                await query.answer("❌ Только создатель может начать игру!", show_alert=True)
        
        elif data.startswith('cancel_'):
            game_code = data.split('_')[1]
            game = word_game_db.get_game(game_code)
            if game and query.from_user.id == game['owner_id']:
                # Уведомляем игроков
                players = word_game_db.get_players(game_code)
                for player in players:
                    try:
                        await context.bot.send_message(
                            chat_id=player['user_id'],
                            text="❌ Игра отменена создателем!"
                        )
                    except:
                        pass
                
                word_game_db.delete_game(game_code)
                await query.edit_message_text("❌ Игра отменена!\n\nВозвращаемся в главное меню...", reply_markup=create_main_widget())
            else:
                await query.answer("❌ Только создатель может отменить игру!", show_alert=True)
        
        elif data.startswith('players_'):
            game_code = data.split('_')[1]
            await show_players(update, context, game_code)
        
        elif data.startswith('showwords_'):
            game_code = data.split('_')[1]
            await show_all_words(update, context, game_code)
        
        elif data.startswith('guess_'):
            game_code = data.split('_')[1]
            await show_player_words(update, context, game_code)
        
        elif data.startswith('giveword_'):
            game_code = data.split('_')[1]
            await give_word_to_player(update, context, game_code)
        
        elif data.startswith('selectplayer_'):
            parts = data.split('_')
            game_code = parts[1]
            target_user_id = int(parts[2])
            await select_player_for_word(update, context, game_code, target_user_id)
        
        elif data.startswith('tryguess_'):
            game_code = data.split('_')[1]
            await query.edit_message_text(
                "🎯 *Попробуйте угадать слово!*\n\n"
                "📝 Напишите ваше предположение одним сообщением.",
                parse_mode='Markdown'
            )
        
        elif data.startswith('back_'):
            game_code = data.split('_')[1]
            await query.edit_message_text(
                "🔙 Возвращаемся к игре...",
                reply_markup=create_game_keyboard(game_code)
            )
        
        elif data.startswith('end_'):
            game_code = data.split('_')[1]
            game = word_game_db.get_game(game_code)
            if game and query.from_user.id == game['owner_id']:
                await end_game(update, context, game_code)
            else:
                await query.answer("❌ Только создатель может завершить игру!", show_alert=True)
        
        elif data.startswith('invite_'):
            game_code = data.split('_')[1]
            game = word_game_db.get_game(game_code)
            if game:
                bot_username = (await context.bot.get_me()).username
                invite_link = f"https://t.me/{bot_username}?start={game_code}"
                
                await query.edit_message_text(
                    text=f"🔗 *Приглашение в игру*\n\n"
                         f"📝 Код: `{game_code}`\n\n"
                         f"📋 *Способы присоединения:*\n"
                         f"1. Нажмите на ссылку:\n"
                         f"`{invite_link}`\n\n"
                         f"2. Отправьте команду:\n"
                         f"`/join {game_code}`",
                    parse_mode='Markdown'
                )
        
        else:
            # Если callback_data не распознан
            await query.edit_message_text(
                "❌ Неизвестная команда. Возвращаюсь в главное меню.",
                reply_markup=create_main_widget()
            )
    
    except Exception as e:
        logger.error(f"Ошибка обработки кнопки: {e}")
        await query.answer("❌ Ошибка!", show_alert=True)

async def handle_my_movies(query, user_id):
    """Обработка кнопки 'Мои фильмы'"""
    want_movies = movies_db.get_user_movies(user_id, status='want_to_watch', limit=5)
    stats = movies_db.get_user_stats(user_id)
    
    text = f"🎬 **Ваши фильмы**\n\n"
    text += f"📝 Хочу посмотреть: {stats['want_count']} фильмов\n"
    text += f"✅ Просмотрено: {stats['watched_count']} фильмов\n\n"
    
    if want_movies:
        text += "🎬 **Последние добавленные:**\n"
        for movie in want_movies:
            text += f"• {movie['title']}"
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            text += "\n"
    
    text += "\nВыберите действие:"
    
    await query.edit_message_text(text, reply_markup=create_movies_widget())

async def handle_watched(query, user_id):
    """Обработка кнопки 'Просмотренные'"""
    watched_movies = movies_db.get_user_movies(user_id, status='watched', limit=10)
    stats = movies_db.get_user_stats(user_id)
    
    text = f"✅ **Просмотренные фильмы ({stats['watched_count']})**\n\n"
    
    if watched_movies:
        for i, movie in enumerate(watched_movies, 1):
            text += f"{i}. {movie['title']}"
            if movie.get('rating'):
                text += f" ⭐{movie['rating']}/10"
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            text += "\n"
        
        if stats['watched_count'] > 10:
            text += f"\n... и еще {stats['watched_count'] - 10} фильмов"
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
        ],
        [
            InlineKeyboardButton("🗑️ Удалить фильм", callback_data="delete_movie"),
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_public_list(query):
    """Обработка кнопки 'Публичный список'"""
    public_movies = movies_db.get_public_movies(limit=10)
    global_stats = movies_db.get_global_stats()
    top_genres = movies_db.get_top_genres(limit=3)
    
    text = "👁️ **Публичный список фильмов**\n\n"
    
    if public_movies:
        text += "🎬 **Последние добавленные:**\n"
        
        for i, movie in enumerate(public_movies[:8], 1):
            user_name = movie['first_name'] or f"User_{movie['user_id']}"
            status_icon = "✅" if movie['status'] == 'watched' else "📝"
            text += f"{i}. {status_icon} {movie['title']}"
            
            if movie.get('genre'):
                text += f" ({movie['genre']})"
            
            if movie.get('year'):
                text += f" [{movie['year']}]"
            
            text += f" — {user_name}\n"
        
        if len(public_movies) > 8:
            text += f"\n... и еще {len(public_movies) - 8} фильмов\n"
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
        ],
        [
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_stats(query, user_id):
    """Обработка кнопки 'Статистика'"""
    user_stats = movies_db.get_user_stats(user_id)
    global_stats = movies_db.get_global_stats()
    
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
        ],
        [
            InlineKeyboardButton("🏆 Топ фильмов", callback_data="top_rated"),
            InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
        ]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_help(query):
    """Обработка кнопки 'Помощь'"""
    help_text = """
🤖 **Универсальный бот - помощь**

🎬 **Управление фильмами:**
• Напишите название фильма, чтобы добавить его
• Используйте кнопки для управления фильмами
• Формат: "Название, жанр, год"
• Удалять фильмы можно через кнопку "🗑️ Удалить фильм"

🎮 **Игра 'Кто я?':**
• Создатель создает игру и приглашает друзей
• Каждый игрок загадывает слово случайному другому игроку
• Все видят слова, загаданные другим игрокам
• Но не видят слово, загаданное им самим
• Задача - угадать свое слово по контексту!

💡 **Важно:**
• Фильмы и игры работают независимо
• Вы можете управлять фильмами в любое время
• Игра не блокирует функции фильмов
"""
    
    keyboard = [
        [InlineKeyboardButton("🏠 Меню", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_current_games(query, user_id, context):
    """Обработка кнопки 'Текущие игры'"""
    # Ищем активные игры пользователя
    games = word_game_db.get_all_games_for_user(user_id)
    
    if not games:
        await query.edit_message_text(
            "📭 *Нет активных игр!*\n"
            "Создайте новую игру или присоединитесь к существующей.",
            reply_markup=create_word_game_main_widget(),
            parse_mode='Markdown'
        )
        return
    
    # Показываем первую активную игру
    game = games[0]
    game_code = game['game_id']
    is_owner = game['owner_id'] == user_id
    
    players = word_game_db.get_players(game_code)
    
    status_texts = {
        'created': '🔄 Ожидание игроков',
        'collecting': '📝 Сбор слов',
        'started': '🎮 Игра идет'
    }
    
    status_text = status_texts.get(game['status'], game['status'])
    
    text = f"🎮 *Текущая игра*\n\n"
    text += f"📝 Код: `{game_code}`\n"
    text += f"📊 Статус: {status_text}\n"
    text += f"👑 Роль: {'Создатель' if is_owner else 'Игрок'}\n\n"
    text += f"👥 *Игроков:* {len(players)}\n"
    
    if game['status'] == 'created':
        await query.edit_message_text(
            text,
            reply_markup=create_game_lobby_keyboard(game_code, is_owner),
            parse_mode='Markdown'
        )
    elif game['status'] == 'collecting':
        await query.edit_message_text(
            text,
            reply_markup=create_waiting_keyboard(game_code),
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            text,
            reply_markup=create_game_keyboard(game_code),
            parse_mode='Markdown'
        )

async def show_movies_for_deletion_callback(query, context):
    """Показать фильмы для удаления (версия для callback)"""
    user_id = query.from_user.id
    
    # Получаем все фильмы пользователя
    movies = movies_db.get_user_movies(user_id, include_private=True, limit=50)
    
    if not movies:
        await query.edit_message_text(
            "📭 У вас нет фильмов для удаления.",
            reply_markup=create_main_widget()
        )
        return
    
    # Сохраняем список фильмов в контексте
    context.user_data['delete_movie_mode'] = True
    context.user_data['delete_movie_list'] = movies
    
    # Формируем сообщение со списком фильмов
    text = "🗑️ **Удаление фильма**\n\n"
    text += "Выберите номер фильма для удаления:\n\n"
    
    for i, movie in enumerate(movies, 1):
        text += f"{i}. "
        
        # Иконка приватности
        text += "👁️ " if movie.get('is_public', True) else "🔒 "
        
        text += f"{movie['title']}"
        
        if movie.get('genre'):
            text += f" ({movie['genre']})"
        
        if movie.get('year'):
            text += f" [{movie['year']}]"
        
        if movie.get('status') == 'watched':
            text += " ✅"
            
            if movie.get('rating'):
                text += f" ⭐{movie['rating']}/10"
        
        text += "\n"
    
    text += "\n📝 **Введите номер фильма для удаления:**"
    text += "\nИли нажмите кнопку 'Отмена'"
    
    await query.edit_message_text(
        text,
        reply_markup=create_delete_movie_widget()
    )

async def handle_watch_button(query, user_id, data):
    """Обработка кнопки 'Просмотрен'"""
    movie_id = int(data.split('_')[1])
    movie = movies_db.get_movie_by_id(user_id, movie_id)
    
    if movie and movie['status'] == 'want_to_watch':
        # Показываем клавиатуру для оценки
        text = f"✅ **Отметить фильм как просмотренный:**\n\n"
        text += f"🎬 {movie['title']}\n\n"
        text += "Поставьте оценку (от 1 до 10):"
        
        await query.edit_message_text(
            text,
            reply_markup=create_rating_widget(movie_id)
        )
    else:
        await query.edit_message_text("❌ Этот фильм уже просмотрен или не найден.")

async def handle_private_button(query, user_id, data):
    """Обработка кнопки 'Приватность'"""
    movie_id = int(data.split('_')[1])
    new_state = movies_db.toggle_movie_privacy(user_id, movie_id)
    
    if new_state is not None:
        movie = movies_db.get_movie_by_id(user_id, movie_id)
        if movie:
            status_text = "публичным" if new_state else "приватным"
            icon = "👁️" if new_state else "🔒"
            
            text = f"✅ Фильм \"{movie['title']}\" теперь {status_text}!\n\nСтатус: {icon} {'Публичный' if new_state else 'Приватный'}"
            
            await query.edit_message_text(
                text,
                reply_markup=create_movie_widget(movie_id)
            )
        else:
            await query.edit_message_text("❌ Фильм не найден.")
    else:
        await query.edit_message_text("❌ Не удалось изменить приватность фильма.")

async def handle_delete_button(query, user_id, data):
    """Обработка кнопки 'Удалить'"""
    movie_id = int(data.split('_')[1])
    success = movies_db.delete_movie(user_id, movie_id)
    
    if success:
        text = "🗑️ Фильм удален из вашего списка!"
        
        keyboard = [
            [
                InlineKeyboardButton("📋 Мои фильмы", callback_data="my_movies"),
                InlineKeyboardButton("➕ Добавить фильм", callback_data="add_movie")
            ],
            [
                InlineKeyboardButton("🏠 Меню", callback_data="main_menu")
            ]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text("❌ Не удалось удалить фильм.")

# ========== ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ ==========
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений"""
    text = update.message.text
    
    # Обновляем активность
    movies_db.update_user_activity(update.effective_user.id)
    
    # Обработка кнопок меню
    if text == "🎬 Управление фильмами":
        await update.message.reply_text(
            "🎬 **Управление фильмами**\n\nВыберите действие:",
            reply_markup=create_main_widget()
        )
    elif text == "🎮 Игра 'Кто я?'":
        await update.message.reply_text(
            "🎮 **Игра 'Кто я?'**\n\n"
            "Выберите действие:",
            reply_markup=create_word_game_main_widget()
        )
    elif text == "❓ Помощь":
        await help_command(update, context)
    else:
        # Проверяем, не находится ли пользователь в режиме удаления фильма
        if 'delete_movie_mode' in context.user_data and context.user_data['delete_movie_mode']:
            # Пользователь в режиме удаления, обрабатываем ввод номера
            await handle_movie_deletion(update, context)
            return
        
        # Проверяем, не отправляет ли пользователь слово для игры
        # Сначала проверяем, участвует ли пользователь в активной игре
        user_id = update.effective_user.id
        games = word_game_db.get_all_games_for_user(user_id)
        
        # Проверяем, есть ли активная игра
        active_game = None
        for game in games:
            if game['status'] in ['collecting', 'started']:
                active_game = game
                break
        
        if active_game:
            # Если есть активная игра, отправляем текст как слово для игры
            await handle_word_submission(update, context)
        else:
            # Если нет активной игры, отправляем текст как фильм
            await add_movie_command(update, context)

# ========== ЗАПУСК БОТА ==========
def main():
    """Запуск бота"""
    print("=" * 50)
    print("🤖 Универсальный бот: Фильмы + Игра 'Кто я?'")
    print("=" * 50)
    print("Инициализация бота...")
    
    # Проверяем токен
    if not BOT_TOKEN or BOT_TOKEN == "8032006876:AAE4b7z902XbYYQQ8VIW2J7kmIHTu8zVkO8":
        print("⚠️  ВНИМАНИЕ: Используется тестовый токен! Убедитесь что он корректен.")
    
    try:
        # Создаем приложение
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("menu", menu_command))  # НОВАЯ КОМАНДА
        application.add_handler(CommandHandler("game", game_command))  # НОВАЯ КОМАНДА
        application.add_handler(CommandHandler("join", join_game))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("add", add_movie_command))
        application.add_handler(CommandHandler("delete", delete_movie_command))
        application.add_handler(CommandHandler("my", show_my_movies_command))
        application.add_handler(CommandHandler("watched", show_watched_command))
        application.add_handler(CommandHandler("public", show_public_list_command))
        application.add_handler(CommandHandler("search", search_command))
        application.add_handler(CommandHandler("search_public", search_public_command))
        application.add_handler(CommandHandler("random", random_movie_command))
        application.add_handler(CommandHandler("stats", show_stats_command))
        
        # Обработчик кнопок
        application.add_handler(CallbackQueryHandler(button_handler))
        
        # Обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
        
        print("✅ Бот инициализирован успешно!")
        print("✅ Базы данных созданы/подключены")
        print("=" * 50)
        print("🚀 Запускаю бота...")
        print("📡 Ожидаю сообщения...")
        print("=" * 50)
        print("Для остановки нажмите Ctrl+C")
        print("=" * 50)
        
        # Запускаем бота
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
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

if __name__ == '__main__':
    main()