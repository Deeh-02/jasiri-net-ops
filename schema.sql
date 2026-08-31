--
-- PostgreSQL database dump
--

\restrict 7IeXqJrJ4PtQfG2412NK9LWyPuOjwj4Ogqe07wq3ZybICQmKO5uAqvgd5HmuwaN

-- Dumped from database version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: batteries; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.batteries (
    id integer NOT NULL,
    battery_number text NOT NULL,
    serial_number text,
    model text,
    capacity text,
    status text DEFAULT 'active'::text,
    created_at timestamp without time zone DEFAULT now(),
    charge_status text DEFAULT 'unknown'::text
);


ALTER TABLE public.batteries OWNER TO postgres;

--
-- Name: batteries_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.batteries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batteries_id_seq OWNER TO postgres;

--
-- Name: batteries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.batteries_id_seq OWNED BY public.batteries.id;


--
-- Name: battery_movements; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.battery_movements (
    id integer NOT NULL,
    battery_id integer,
    from_location_id integer,
    to_location_id integer,
    reason text,
    moved_by text,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.battery_movements OWNER TO postgres;

--
-- Name: battery_movements_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.battery_movements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.battery_movements_id_seq OWNER TO postgres;

--
-- Name: battery_movements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.battery_movements_id_seq OWNED BY public.battery_movements.id;


--
-- Name: locations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.locations (
    id integer NOT NULL,
    name text NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    is_home_base boolean DEFAULT false NOT NULL,
    contact_name text,
    contact_phone text,
    address text
);


ALTER TABLE public.locations OWNER TO postgres;

--
-- Name: locations_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.locations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.locations_id_seq OWNER TO postgres;

--
-- Name: locations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.locations_id_seq OWNED BY public.locations.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    id integer NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    phone text,
    password_hash text NOT NULL,
    role text DEFAULT 'technician'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO postgres;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: batteries id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batteries ALTER COLUMN id SET DEFAULT nextval('public.batteries_id_seq'::regclass);


--
-- Name: battery_movements id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.battery_movements ALTER COLUMN id SET DEFAULT nextval('public.battery_movements_id_seq'::regclass);


--
-- Name: locations id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.locations ALTER COLUMN id SET DEFAULT nextval('public.locations_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: batteries batteries_battery_number_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batteries
    ADD CONSTRAINT batteries_battery_number_key UNIQUE (battery_number);


--
-- Name: batteries batteries_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batteries
    ADD CONSTRAINT batteries_pkey PRIMARY KEY (id);


--
-- Name: battery_movements battery_movements_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.battery_movements
    ADD CONSTRAINT battery_movements_pkey PRIMARY KEY (id);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: one_home_base_idx; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX one_home_base_idx ON public.locations USING btree (is_home_base) WHERE (is_home_base = true);


--
-- Name: battery_movements battery_movements_battery_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.battery_movements
    ADD CONSTRAINT battery_movements_battery_id_fkey FOREIGN KEY (battery_id) REFERENCES public.batteries(id);


--
-- Name: battery_movements battery_movements_from_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.battery_movements
    ADD CONSTRAINT battery_movements_from_location_id_fkey FOREIGN KEY (from_location_id) REFERENCES public.locations(id);


--
-- Name: battery_movements battery_movements_to_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.battery_movements
    ADD CONSTRAINT battery_movements_to_location_id_fkey FOREIGN KEY (to_location_id) REFERENCES public.locations(id);


--
-- PostgreSQL database dump complete
--

\unrestrict 7IeXqJrJ4PtQfG2412NK9LWyPuOjwj4Ogqe07wq3ZybICQmKO5uAqvgd5HmuwaN

